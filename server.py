import http.server
import socketserver
import json
import os
import threading
import subprocess

def load_env_file():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.environ.get('HH_ENV_FILE') or os.path.join(script_dir, '.env')
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, v = line.split('=', 1)
                        os.environ[k.strip()] = v.strip().strip('\"\'')
        except Exception as e:
            print('Error loading .env file:', e)
BASE_DIR = os.environ.get("HH_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
SCREENSHOTS_DIR = os.path.join(LOGS_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BASE_DIR, "resumes_config.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")
VENV_PYTHON = os.environ.get("HH_VENV_PYTHON") or os.path.join(BASE_DIR, "venv/bin/python")

PORT = int(os.environ.get("HH_DASHBOARD_PORT", 8883))

running_jobs = {} # {resume_id: status_msg}

def load_resumes_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    data = {"auth": {"email": os.environ.get("HH_EMAIL", ""), "password": os.environ.get("HH_PASSWORD", "")}, "resumes": data}
                
                # Fill auth from env if empty
                if not data.get("auth", {}).get("email"):
                    data["auth"] = data.get("auth", {})
                    data["auth"]["email"] = os.environ.get("HH_EMAIL", "")
                if not data.get("auth", {}).get("password"):
                    data["auth"] = data.get("auth", {})
                    data["auth"]["password"] = os.environ.get("HH_PASSWORD", "")
                return data
        except Exception as e:
            print("Error loading config:", e)
    return {
        "auth": {"email": os.environ.get("HH_EMAIL", ""), "password": os.environ.get("HH_PASSWORD", "")},
        "resumes": []
    }

def save_resumes_config(cfg_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, ensure_ascii=False, indent=2)
        pass
        sync_crontab_with_config(cfg_data)
    except Exception as e:
        print("Error saving config:", e)

def sync_crontab_with_config(cfg_data):
    resumes = cfg_data.get("resumes", [])
    if not resumes:
        print("No resumes in config, skipping crontab sync.")
        return
    cron_lines = []
    for item in resumes:
        if not item.get("enabled", True):
            continue
        resume_id = item.get("id")
        schedule = item.get("schedule", [])
        log_file = os.path.join(LOGS_DIR, f"{resume_id}.log")
        
        for t_str in schedule:
            t_str = t_str.strip()
            if ":" in t_str:
                parts = t_str.split(":")
                try:
                    h = int(parts[0])
                    m = int(parts[1])
                    cron_line = f"{m} {h} * * * cd {BASE_DIR} && {VENV_PYTHON} main.py --resume-id {resume_id}"
                    cron_lines.append(cron_line)
                except ValueError:
                    pass

    if not cron_lines:
        print("No valid cron entries generated, skipping crontab overwrite.")
        return

    # Preserve non-HH lines (e.g. tunnel_watchdog.sh) from current crontab
    try:
        existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
    except subprocess.CalledProcessError:
        existing = ""
    preserved = [l for l in existing.splitlines() if l.strip() and "main.py" not in l]
    all_lines = preserved + cron_lines
    new_crontab = "\n".join(all_lines) + "\n"

    tmp_cron = "/tmp/new_crontab.txt"
    try:
        with open(tmp_cron, "w", encoding="utf-8") as f:
            f.write(new_crontab)
        cron_user = os.environ.get("HH_CRON_USER") or os.environ.get("SUDO_USER")
        cmd_args = ["crontab", "-u", cron_user, tmp_cron] if cron_user else ["crontab", tmp_cron]
        subprocess.run(cmd_args, check=True)
        if os.path.exists(tmp_cron):
            os.remove(tmp_cron)
        print("Crontab updated successfully!")
    except Exception as e:
        print("Error updating crontab:", e)

def run_hh_update_thread(resume_id):
    global running_jobs
    # Check lock before starting — prevents double-run when cron fires at the same moment
    import fcntl
    lock_filename = f"/tmp/hh_autoupdate_{resume_id}.lock"
    lock_file = None
    try:
        fd = os.open(lock_filename, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o666)
        lock_file = os.fdopen(fd, "a+")
        try:
            os.chmod(lock_filename, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError, BlockingIOError):
        print(f"[server] resume_id={resume_id} already running (lock busy), skipping.")
        if lock_file:
            lock_file.close()
        if resume_id in running_jobs:
            del running_jobs[resume_id]
        return

    running_jobs[resume_id] = "Поднятие резюме..."
    try:
        cron_user = os.environ.get("HH_CRON_USER") or os.environ.get("SUDO_USER")
        sudo_prefix = f"sudo -u {cron_user} " if (cron_user and os.geteuid() == 0) else ""
        cmd = f"cd {BASE_DIR} && {sudo_prefix}{VENV_PYTHON} main.py --resume-id {resume_id}"
        subprocess.run(cmd, shell=True, check=True)
    except Exception as e:
        print(f"Error running update for {resume_id}:", e)
    finally:
        if lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
            except Exception:
                pass
        if resume_id in running_jobs:
            del running_jobs[resume_id]

INDEX_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HH Resume Auto-Update Control Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0f172a;
            --card-bg: #1e293b;
            --accent-blue: #3b82f6;
            --accent-blue-hover: #2563eb;
            --accent-green: #10b981;
            --accent-red: #ef4444;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border-color: #334155;
        }
        * { box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 30px 15px;
            min-height: 100vh;
        }
        .container { max-width: 1280px; margin: 0 auto; }
        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
        }
        .header h1 { font-size: 22px; margin: 0; font-weight: 700; }
        
        .auth-card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px;
            border: 1px solid var(--border-color);
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            align-items: flex-end;
        }
        .auth-title {
            width: 100%;
            font-size: 15px;
            font-weight: 700;
            margin: 0;
            color: #38bdf8;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(540px, 1fr));
            gap: 24px;
        }
        .card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 24px;
            border: 1px solid var(--border-color);
            box-shadow: 0 10px 25px rgba(0,0,0,0.3);
            display: flex;
            flex-direction: column;
            gap: 16px;
        }
        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .switch {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 26px;
        }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #475569;
            transition: .3s;
            border-radius: 26px;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 20px;
            width: 20px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .3s;
            border-radius: 50%;
        }
        input:checked + .slider { background-color: var(--accent-green); }
        input:checked + .slider:before { transform: translateX(24px); }

        .field-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
            flex: 1;
            min-width: 200px;
        }
        .field-label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .input-text {
            width: 100%;
            background: #0f172a;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 10px 12px;
            color: var(--text-main);
            font-size: 13.5px;
            font-family: inherit;
        }
        .input-text:focus {
            outline: none;
            border-color: var(--accent-blue);
        }
        .input-text.dirty {
            border-color: #f59e0b;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(16, 185, 129, 0.15);
            color: var(--accent-green);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 12.5px;
            font-weight: 600;
        }
        .status-badge.disabled {
            background: rgba(239, 68, 68, 0.15);
            color: var(--accent-red);
        }

        .btn-group {
            display: flex;
            gap: 12px;
        }
        .btn {
            padding: 10px 16px;
            border-radius: 10px;
            font-size: 13.5px;
            font-weight: 600;
            border: none;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
        }
        .btn-save { background: #334155; color: #ffffff; }
        .btn-save:hover { background: #475569; }
        .btn-trigger { background: var(--accent-blue); color: #ffffff; flex:1; }
        .btn-trigger:hover { background: var(--accent-blue-hover); }
        .btn-add { background: var(--accent-green); color: #ffffff; }
        .btn-add:hover { background: #059669; }
        .btn-delete { background: rgba(239, 68, 68, 0.2); color: var(--accent-red); padding: 6px 12px; font-size: 12px; }
        .btn-delete:hover { background: rgba(239, 68, 68, 0.4); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .log-box {
            background: #090d16;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 14px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 12px;
            line-height: 1.5;
            color: #38bdf8;
            height: 380px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
        }
        .log-box::-webkit-scrollbar { width: 8px; }
        .log-box::-webkit-scrollbar-track { background: #0b0f19; border-radius: 4px; }
        .log-box::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        .log-box::-webkit-scrollbar-thumb:hover { background: #475569; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>💼 HH Resume Auto-Update Control Center</h1>
                <div style="font-size: 13px; color: var(--text-muted); margin-top:4px;">Управление аккаунтом и автоподнятием резюме (MSK)</div>
            </div>
            <div style="display:flex; gap:10px;">
                <button class="btn btn-add" onclick="addNewResume()">➕ Добавить резюме</button>
                <button class="btn btn-save" onclick="loadData()">🔄 Обновить статус</button>
            </div>
        </div>

        <div class="auth-card">
            <h3 class="auth-title">🔐 Авторизация аккаунта HH.ru</h3>
            <div class="field-group">
                <label class="field-label">Email аккаунта</label>
                <input type="email" class="input-text" id="auth-email" placeholder="example@ya.ru" onchange="markDirty('auth-email')" onblur="handleBlurAuth('auth-email')">
            </div>
            <div class="field-group">
                <label class="field-label">Пароль аккаунта</label>
                <input type="password" class="input-text" id="auth-password" placeholder="••••••••" onchange="markDirty('auth-password')" onblur="handleBlurAuth('auth-password')">
            </div>
            <button class="btn btn-save" onclick="saveAuth()">💾 Сохранить данные входа</button>
        </div>

        <div class="grid" id="resumes-container">
            <!-- Dynamic Resume Cards -->
        </div>
    </div>

    <script>
        let currentFullConfig = { auth: {}, resumes: [] };
        let loadedResumeIds = [];
        let dirtyFields = {}; // { elementId: originalValue }

        function markDirty(elId) {
            const el = document.getElementById(elId);
            if (el) {
                el.classList.add('dirty');
                dirtyFields[elId] = true;
            }
        }

        function clearDirty(elId) {
            const el = document.getElementById(elId);
            if (el) {
                el.classList.remove('dirty');
                delete dirtyFields[elId];
            }
        }

        async function handleBlurResume(id, fieldType) {
            const elId = fieldType + '-' + id;
            if (dirtyFields[elId]) {
                const item = currentFullConfig.resumes.find(r => r.id === id);
                const fieldName = fieldType === 'name' ? 'название' : (fieldType === 'url' ? 'ссылку' : 'время');
                const targetTitle = item ? (item.name || id) : id;
                
                if (confirm('Вы изменили ' + fieldName + ' для "' + targetTitle + '". Сохранить изменения?')) {
                    await saveResume(id);
                } else {
                    clearDirty(elId);
                    await loadData();
                }
            }
        }

        async function handleBlurAuth(fieldId) {
            if (dirtyFields[fieldId]) {
                if (confirm('Вы изменили данные входа HH.ru. Сохранить авторизацию?')) {
                    await saveAuth();
                } else {
                    clearDirty(fieldId);
                    await loadData();
                }
            }
        }

        async function loadData() {
            try {
                const res = await fetch('/api/config');
                currentFullConfig = await res.json();
                
                const emailInp = document.getElementById('auth-email');
                if (emailInp && !dirtyFields['auth-email'] && document.activeElement !== emailInp) {
                    emailInp.value = currentFullConfig.auth?.email || '';
                }
                const passInp = document.getElementById('auth-password');
                if (passInp && !dirtyFields['auth-password'] && document.activeElement !== passInp) {
                    passInp.value = currentFullConfig.auth?.password || '';
                }

                updateUI(currentFullConfig.resumes || []);
            } catch (err) {
                console.error("Error loading config:", err);
            }
        }

        function updateUI(resumesList) {
            const container = document.getElementById('resumes-container');
            const currentIds = resumesList.map(r => r.id);

            if (JSON.stringify(currentIds) !== JSON.stringify(loadedResumeIds)) {
                container.innerHTML = '';
                resumesList.forEach((item, index) => {
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.id = 'card-' + item.id;
                    card.innerHTML = `
                        <div class="card-header">
                            <input type="text" class="input-text" style="font-weight:700; font-size:16px; width:65%;" id="name-${item.id}" value="${item.name || ''}" placeholder="Название резюме" oninput="markDirty('name-${item.id}')" onblur="handleBlurResume('${item.id}', 'name')">
                            <div style="display:flex; align-items:center; gap:12px;">
                                <label class="switch">
                                    <input type="checkbox" id="enable-${item.id}" onchange="toggleEnable('${item.id}')">
                                    <span class="slider"></span>
                                </label>
                                <button class="btn btn-delete" onclick="deleteResume('${item.id}')">🗑️ Удалить</button>
                            </div>
                        </div>

                        <div class="field-group">
                            <label class="field-label">Ссылка на резюме (HH URL)</label>
                            <input type="text" class="input-text" id="url-${item.id}" placeholder="https://krasnodar.hh.ru/resume/..." oninput="markDirty('url-${item.id}')" onblur="handleBlurResume('${item.id}', 'url')">
                        </div>

                        <div class="field-group">
                            <label class="field-label">Время автоподъема (ЧЧ:ММ MSK через запятую)</label>
                            <input type="text" class="input-text" id="sched-${item.id}" placeholder="07:00, 11:01, 15:02, 19:03, 23:04" oninput="markDirty('sched-${item.id}')" onblur="handleBlurResume('${item.id}', 'sched')">
                        </div>

                        <div style="display:flex; justify-content:space-between; align-items:center;">
                            <span class="status-badge" id="badge-${item.id}"></span>
                            <div style="font-size:12px; color:var(--text-muted);" id="statustext-${item.id}"></div>
                        </div>

                        <div class="btn-group">
                            <button class="btn btn-save" onclick="saveResume('${item.id}')">💾 Сохранить настройки</button>
                            <button class="btn btn-trigger" id="btn-trig-${item.id}" onclick="triggerResume('${item.id}')"></button>
                        </div>

                        <div class="field-group">
                            <label class="field-label">📜 Лог выполнения</label>
                            <div class="log-box" id="logbox-${item.id}"></div>
                        </div>
                    `;
                    container.appendChild(card);
                });
                loadedResumeIds = currentIds;
            }

            resumesList.forEach((item) => {
                const isEnabled = item.enabled;
                const isRunning = item.is_running;
                const schedStr = (item.schedule || []).join(', ');

                const nameInput = document.getElementById('name-' + item.id);
                if (nameInput && !dirtyFields['name-' + item.id] && document.activeElement !== nameInput) {
                    nameInput.value = item.name || '';
                }

                const enableInput = document.getElementById('enable-' + item.id);
                if (enableInput) enableInput.checked = isEnabled;

                const urlInput = document.getElementById('url-' + item.id);
                if (urlInput && !dirtyFields['url-' + item.id] && document.activeElement !== urlInput) {
                    urlInput.value = item.url || '';
                }

                const schedInput = document.getElementById('sched-' + item.id);
                if (schedInput && !dirtyFields['sched-' + item.id] && document.activeElement !== schedInput) {
                    schedInput.value = schedStr;
                }

                const badge = document.getElementById('badge-' + item.id);
                if (badge) {
                    badge.className = 'status-badge ' + (isEnabled ? '' : 'disabled');
                    badge.textContent = isEnabled ? '🟢 Автоподъем активен' : '🔴 Автоподъем выключен';
                }

                const statustext = document.getElementById('statustext-' + item.id);
                if (statustext) {
                    statustext.innerHTML = `Статус: <b>${item.last_result || '-'}</b> (${item.last_time || '-'})`;
                }

                const btn = document.getElementById('btn-trig-' + item.id);
                if (btn) {
                    btn.disabled = isRunning;
                    btn.textContent = isRunning ? '⏳ Поднятие...' : '🚀 Поднять сейчас';
                }

                const lb = document.getElementById('logbox-' + item.id);
                if (lb) {
                    const newLog = item.log || 'Лог пуст';
                    if (lb.textContent !== newLog) {
                        const isNearBottom = (lb.scrollHeight - lb.scrollTop - lb.clientHeight) < 80;
                        lb.textContent = newLog;
                        if (isNearBottom || lb.scrollTop === 0) {
                            lb.scrollTop = lb.scrollHeight;
                        }
                    }
                }
            });
        }

        async function saveAuth() {
            const email = document.getElementById('auth-email').value.trim();
            const password = document.getElementById('auth-password').value.trim();
            if (!email || !password) {
                alert('Заполните Email и Пароль');
                return;
            }
            currentFullConfig.auth = { email, password };
            clearDirty('auth-email');
            clearDirty('auth-password');
            await saveConfigToServer();
            alert('Учетные данные HH.ru сохранены!');
        }

        async function addNewResume() {
            const newId = 'resume_' + Date.now();
            const count = (currentFullConfig.resumes || []).length + 1;
            const newResume = {
                id: newId,
                name: 'Резюме ' + count,
                url: '',
                enabled: true,
                schedule: ['07:00', '11:01', '15:02', '19:03', '23:04'],
                last_time: '-',
                last_result: 'Новое резюме',
                log_file: 'logs/' + newId + '.log'
            };
            currentFullConfig.resumes.push(newResume);
            await saveConfigToServer();
        }

        async function deleteResume(id) {
            const item = currentFullConfig.resumes.find(r => r.id === id);
            const name = item ? item.name : id;
            if (confirm('Вы уверены, что хотите удалить ' + name + '?')) {
                currentFullConfig.resumes = currentFullConfig.resumes.filter(r => r.id !== id);
                await saveConfigToServer();
            }
        }

        async function toggleEnable(id) {
            const item = currentFullConfig.resumes.find(r => r.id === id);
            if (item) {
                item.enabled = document.getElementById('enable-' + id).checked;
                await saveConfigToServer();
            }
        }

        async function saveResume(id) {
            const item = currentFullConfig.resumes.find(r => r.id === id);
            if (item) {
                const nameInp = document.getElementById('name-' + id);
                const urlInp = document.getElementById('url-' + id);
                const schedInp = document.getElementById('sched-' + id);

                if (nameInp) item.name = nameInp.value.trim();
                if (urlInp) item.url = urlInp.value.trim();
                if (schedInp) {
                    const rawSched = schedInp.value.trim();
                    item.schedule = rawSched.split(',').map(s => s.trim()).filter(s => s.length > 0);
                }

                clearDirty('name-' + id);
                clearDirty('url-' + id);
                clearDirty('sched-' + id);

                await saveConfigToServer();
            }
        }

        async function saveConfigToServer() {
            try {
                await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(currentFullConfig)
                });
                await loadData();
            } catch (err) {
                alert('Ошибка при сохранении на сервер');
            }
        }

        async function triggerResume(id) {
            const btn = document.getElementById('btn-trig-' + id);
            if (btn) btn.disabled = true;

            try {
                const res = await fetch('/api/trigger', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ resume_id: id })
                });
                const data = await res.json();
                if (!data.success) {
                    alert(data.message || 'Ошибка запуска');
                }
                setTimeout(loadData, 1000);
            } catch (err) {
                alert('Ошибка сети при поднять резюме');
            }
        }

        setInterval(loadData, 4000);
        loadData();
    </script>
</body>
</html>"""

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Connection", "close")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b""
        
        try:
            body = json.loads(body_bytes.decode('utf-8')) if body_bytes else {}
        except Exception:
            body = {}

        if self.path == "/api/config":
            if isinstance(body, dict):
                save_resumes_config(body)
                res = json.dumps({"success": True}).encode("utf-8")
            else:
                res = json.dumps({"success": False, "message": "Invalid format"}).encode("utf-8")

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        if self.path == "/api/session":
            session_data = body.get("session")
            if session_data:
                try:
                    session_file = os.path.join(BASE_DIR, "hh_session.json")
                    if isinstance(session_data, str):
                        try:
                            parsed = json.loads(session_data)
                            session_data = parsed
                        except Exception:
                            session_data = {
                                "cookies": [
                                    {
                                        "name": "hhtoken",
                                        "value": session_data.strip(),
                                        "domain": ".hh.ru",
                                        "path": "/"
                                    }
                                ],
                                "origins": []
                            }

                    with open(session_file, "w", encoding="utf-8") as f:
                        json.dump(session_data, f, ensure_ascii=False, indent=2)
                    pass
                    res = json.dumps({"success": True, "message": "Сессия HH.ru успешно сохранена!"}).encode("utf-8")
                except Exception as e:
                    res = json.dumps({"success": False, "message": f"Ошибка сохранения: {e}"}).encode("utf-8")
            else:
                res = json.dumps({"success": False, "message": "Данные сессии пусты"}).encode("utf-8")

            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        if self.path == "/api/trigger":
            resume_id = body.get("resume_id", "resume1")
            if resume_id in running_jobs:
                res = json.dumps({"success": False, "message": "Автоподъем уже выполняется"}).encode("utf-8")
            else:
                t = threading.Thread(target=run_hh_update_thread, args=(resume_id,), daemon=True)
                t.start()
                res = json.dumps({"success": True}).encode("utf-8")
            
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/":
            encoded = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return

        if self.path == "/api/config":
            cfg_data = load_resumes_config()
            cron_log_path = os.path.join(LOGS_DIR, "cron_log.log")
            resumes = cfg_data.get("resumes", [])
            
            for item in resumes:
                resume_id = item.get("id")
                log_file = os.path.join(LOGS_DIR, f"{resume_id}.log")
                log_content = ""
                
                if os.path.exists(log_file):
                    try:
                        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                            log_content = "".join(lines[-150:])
                    except Exception:
                        pass

                if resume_id == "resume1" and len(log_content.strip()) < 50 and os.path.exists(cron_log_path):
                    try:
                        with open(cron_log_path, "r", encoding="utf-8", errors="ignore") as f:
                            lines = f.readlines()
                            log_content = "".join(lines[-150:])
                    except Exception:
                        pass

                item["log"] = log_content
                item["is_running"] = resume_id in running_jobs

            cfg_data["resumes"] = resumes
            res = json.dumps(cfg_data).encode("utf-8")
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(res)))
            self.end_headers()
            self.wfile.write(res)
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

if __name__ == "__main__":
    cfg_data = load_resumes_config()
    sync_crontab_with_config(cfg_data)
    with ThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler) as httpd:
        print(f"HH Multi-Resume Dashboard started on port {PORT}")
        httpd.serve_forever()
