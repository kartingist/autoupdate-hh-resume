import http.server
import socketserver
import json
import os
import threading
import subprocess
import fcntl
import tempfile
import base64
import re

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

load_env_file()

BASE_DIR = os.environ.get("HH_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
SCREENSHOTS_DIR = os.path.join(LOGS_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BASE_DIR, "resumes_config.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")
VENV_PYTHON = os.environ.get("HH_VENV_PYTHON") or os.path.join(BASE_DIR, "venv/bin/python")

PORT = int(os.environ.get("HH_DASHBOARD_PORT", 8883))

# Basic Authentication Settings
DASHBOARD_USER = os.environ.get("HH_DASHBOARD_USER", "")
DASHBOARD_PASS = os.environ.get("HH_DASHBOARD_PASS", "")

def is_authenticated(headers) -> bool:
    if not DASHBOARD_USER or not DASHBOARD_PASS:
        return True  # Auth is disabled if credentials not set
    auth_header = headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        return user == DASHBOARD_USER and pwd == DASHBOARD_PASS
    except Exception:
        return False

def is_valid_resume_id(resume_id: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", str(resume_id)))

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
    lock_cfg_path = os.path.join(BASE_DIR, "resumes_config.lock")
    lock_fd = None
    try:
        lock_fd = open(lock_cfg_path, "a+")
        try:
            os.chmod(lock_cfg_path, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Merge password and resume statuses from disk to avoid race conditions and masking overwrite
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
                    if isinstance(disk_data, dict):
                        # Password protection: if password was masked or empty, retain existing
                        submitted_pwd = cfg_data.get("auth", {}).get("password", "").strip()
                        disk_pwd = disk_data.get("auth", {}).get("password") or os.environ.get("HH_PASSWORD", "")
                        if not submitted_pwd or submitted_pwd in ("••••••••", "********"):
                            if "auth" not in cfg_data:
                                cfg_data["auth"] = {}
                            cfg_data["auth"]["password"] = disk_pwd

                        # Merge last_time / last_result
                        disk_resumes = {r["id"]: r for r in disk_data.get("resumes", []) if isinstance(r, dict) and "id" in r}
                        for r in cfg_data.get("resumes", []):
                            r_id = r.get("id")
                            if r_id in disk_resumes:
                                if not r.get("last_time") or r.get("last_time") == "-":
                                    r["last_time"] = disk_resumes[r_id].get("last_time", "-")
                                if not r.get("last_result") or r.get("last_result") == "Новое резюме":
                                    r["last_result"] = disk_resumes[r_id].get("last_result", "Новое резюме")
            except Exception as merge_err:
                print("Warning merging config with disk state:", merge_err)

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg_data, f, ensure_ascii=False, indent=2)

        sync_crontab_with_config(cfg_data)
    except Exception as e:
        print("Error saving config:", e)
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass

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
        if not is_valid_resume_id(resume_id):
            continue
        schedule = item.get("schedule", [])
        
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

    # Preserve non-HH lines from current crontab
    try:
        existing = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode()
    except subprocess.CalledProcessError:
        existing = ""
    preserved = [l for l in existing.splitlines() if l.strip() and "main.py" not in l]
    all_lines = preserved + cron_lines
    new_crontab = "\n".join(all_lines) + "\n"

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
            tf.write(new_crontab)
            tmp_path = tf.name

        cron_user = os.environ.get("HH_CRON_USER") or "root"
        cmd_args = ["crontab", "-u", cron_user, tmp_path] if cron_user else ["crontab", tmp_path]
        subprocess.run(cmd_args, check=True)
        print("Crontab updated successfully!")
    except Exception as e:
        print("Error updating crontab:", e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

def run_hh_update_thread(resume_id):
    global running_jobs
    if not is_valid_resume_id(resume_id):
        print(f"Invalid resume_id rejected: {resume_id}")
        return

    running_jobs[resume_id] = "Поднятие резюме..."
    try:
        cron_user = os.environ.get("HH_CRON_USER") or "root"
        cmd = [VENV_PYTHON, "main.py", "--resume-id", resume_id]
        if cron_user and os.geteuid() == 0 and cron_user != "root":
            cmd = ["sudo", "-u", cron_user] + cmd
        
        subprocess.run(cmd, cwd=BASE_DIR, check=True)
    except Exception as e:
        print(f"Error running update for {resume_id}:", e)
    finally:
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
            --bg-color: #0b1120;
            --card-bg: #1e293b;
            --card-bg-subtle: #172033;
            --accent-blue: #38bdf8;
            --accent-blue-hover: #0ea5e9;
            --accent-green: #10b981;
            --accent-green-hover: #059669;
            --accent-red: #ef4444;
            --accent-amber: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border-color: #334155;
            --border-color-focus: #38bdf8;
        }
        * { box-sizing: border-box; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-color);
            color: var(--text-main);
            margin: 0;
            padding: 30px 16px;
            min-height: 100vh;
        }
        .container { max-width: 1280px; margin: 0 auto; }
        
        /* Focus visible for accessibility */
        *:focus-visible {
            outline: 2px solid var(--accent-blue);
            outline-offset: 2px;
        }

        .header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 24px;
            padding-bottom: 18px;
            border-bottom: 1px solid var(--border-color);
            flex-wrap: wrap;
            gap: 16px;
        }
        .header-title-box { display: flex; flex-direction: column; gap: 4px; }
        .header h1 { font-size: 22px; margin: 0; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .system-tags {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 6px;
            flex-wrap: wrap;
        }
        .tag-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            font-size: 11.5px;
            font-weight: 600;
            padding: 3px 9px;
            border-radius: 12px;
            background: #1e293b;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
        }
        .tag-badge.active {
            background: rgba(16, 185, 129, 0.12);
            border-color: rgba(16, 185, 129, 0.3);
            color: var(--accent-green);
        }

        .auth-card {
            background: var(--card-bg);
            border-radius: 16px;
            padding: 20px 24px;
            border: 1px solid var(--border-color);
            margin-bottom: 24px;
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            align-items: flex-end;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        }
        .auth-title {
            width: 100%;
            font-size: 15px;
            font-weight: 700;
            margin: 0;
            color: var(--accent-blue);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr));
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
            transition: border-color 0.2s;
        }
        .card:hover {
            border-color: #475569;
        }
        .card-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }
        
        .switch {
            position: relative;
            display: inline-block;
            width: 48px;
            height: 26px;
            flex-shrink: 0;
        }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0; left: 0; right: 0; bottom: 0;
            background-color: #475569;
            transition: .25s ease-in-out;
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
            transition: .25s ease-in-out;
            border-radius: 50%;
        }
        input:checked + .slider { background-color: var(--accent-green); }
        input:checked + .slider:before { transform: translateX(22px); }

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
            transition: border-color 0.2s;
        }
        .input-text:focus {
            outline: none;
            border-color: var(--border-color-focus);
        }
        .input-text.dirty {
            border-color: var(--accent-amber);
            box-shadow: 0 0 0 1px rgba(245, 158, 11, 0.2);
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
            flex-wrap: wrap;
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
            min-height: 40px;
        }
        .btn-save { background: #334155; color: #ffffff; }
        .btn-save:hover { background: #475569; }
        .btn-trigger { background: var(--accent-blue); color: #090d16; font-weight: 700; flex: 1; }
        .btn-trigger:hover { background: var(--accent-blue-hover); color: #ffffff; }
        .btn-add { background: var(--accent-green); color: #ffffff; }
        .btn-add:hover { background: var(--accent-green-hover); }
        .btn-delete { background: rgba(239, 68, 68, 0.15); color: var(--accent-red); padding: 6px 12px; font-size: 12px; }
        .btn-delete:hover { background: rgba(239, 68, 68, 0.35); }
        .btn-toggle-log {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-toggle-log:hover {
            color: var(--text-main);
            border-color: #64748b;
        }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }

        .log-box {
            background: #060911;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 12px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 12px;
            line-height: 1.5;
            color: #38bdf8;
            height: 160px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            transition: height 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .log-box.expanded {
            height: 380px;
        }
        .log-box::-webkit-scrollbar { width: 6px; }
        .log-box::-webkit-scrollbar-track { background: #0b0f19; }
        .log-box::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
        .log-box::-webkit-scrollbar-thumb:hover { background: #475569; }

        /* Toast notifications container */
        #toast-container {
            position: fixed;
            top: 24px;
            right: 24px;
            z-index: 99999;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: 380px;
            width: calc(100% - 48px);
            pointer-events: none;
        }
        .toast {
            pointer-events: auto;
            background: rgba(30, 41, 59, 0.95);
            backdrop-filter: blur(10px);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 14px 18px;
            color: var(--text-main);
            font-size: 13.5px;
            font-weight: 500;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            gap: 12px;
            animation: slideInRight 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
            transition: opacity 0.3s, transform 0.3s;
        }
        .toast.hide {
            opacity: 0;
            transform: translateX(40px);
        }
        .toast-success { border-left: 4px solid var(--accent-green); }
        .toast-error { border-left: 4px solid var(--accent-red); }
        .toast-info { border-left: 4px solid var(--accent-blue); }

        @keyframes slideInRight {
            from { opacity: 0; transform: translateX(50px); }
            to { opacity: 1; transform: translateX(0); }
        }

        /* Modal Dialog */
        .modal-overlay {
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(11, 17, 32, 0.75);
            backdrop-filter: blur(6px);
            z-index: 99998;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
        }
        .modal-overlay.active {
            opacity: 1;
            pointer-events: auto;
        }
        .modal-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            max-width: 460px;
            width: 100%;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
            display: flex;
            flex-direction: column;
            gap: 16px;
            transform: scale(0.95);
            transition: transform 0.2s ease;
        }
        .modal-overlay.active .modal-card {
            transform: scale(1);
        }
        .modal-title { margin: 0; font-size: 17px; font-weight: 700; }
        .modal-body { font-size: 14px; color: var(--text-muted); line-height: 1.5; margin: 0; }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 8px; }

        /* Empty state */
        .empty-card {
            grid-column: 1 / -1;
            background: var(--card-bg-subtle);
            border: 2px dashed var(--border-color);
            border-radius: 16px;
            padding: 48px 24px;
            text-align: center;
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 14px;
        }

        /* Mobile adaptivity */
        @media (max-width: 640px) {
            body { padding: 18px 12px; }
            .header { flex-direction: column; align-items: flex-start; gap: 14px; }
            .auth-card { padding: 16px; }
            .card { padding: 18px; }
            .card-header { flex-wrap: wrap; }
            .card-header input { width: 100% !important; margin-bottom: 8px; }
            .btn-group { flex-direction: column; }
            .btn { width: 100%; min-height: 44px; }
            .btn-trigger { order: -1; }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-title-box">
                <h1>💼 HH Resume Auto-Update Control Center</h1>
                <div style="font-size: 13px; color: var(--text-muted);">Интеллектуальный автоподъем резюме на HeadHunter (MSK)</div>
                <div class="system-tags">
                    <span id="tg-status-tag" class="tag-badge">🤖 Telegram: проверка...</span>
                    <span id="session-status-tag" class="tag-badge">🔐 Сессия: проверка...</span>
                </div>
            </div>
            <div style="display:flex; gap:10px; flex-wrap:wrap;">
                <button class="btn btn-add" onclick="addNewResume()">➕ Добавить резюме</button>
                <button class="btn btn-save" onclick="manualRefresh()">🔄 Обновить статус</button>
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
            <!-- Dynamic Resume Cards / Empty State -->
        </div>
    </div>

    <!-- Toast Notifications Container -->
    <div id="toast-container"></div>

    <!-- Confirm Dialog Modal -->
    <div id="confirm-modal" class="modal-overlay">
        <div class="modal-card">
            <h3 class="modal-title" id="confirm-modal-title">Подтверждение</h3>
            <p class="modal-body" id="confirm-modal-text"></p>
            <div class="modal-actions">
                <button class="btn btn-save" id="confirm-btn-cancel">Отмена</button>
                <button class="btn btn-trigger" id="confirm-btn-ok">Подтвердить</button>
            </div>
        </div>
    </div>

    <script>
        let currentFullConfig = { auth: {}, resumes: [], system: {} };
        let loadedResumeIds = [];
        let dirtyFields = {}; // { elementId: true }
        let logExpandedState = {}; // { resumeId: bool }

        // --- Toast System ---
        function showToast(message, type = 'info') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            
            let icon = 'ℹ️';
            if (type === 'success') icon = '✅';
            if (type === 'error') icon = '⚠️';
            
            toast.innerHTML = `<span style="font-size:16px;">${icon}</span><span>${message}</span>`;
            container.appendChild(toast);
            
            setTimeout(() => {
                toast.classList.add('hide');
                setTimeout(() => toast.remove(), 320);
            }, 3500);
        }

        // --- Modal Confirmation System ---
        function showConfirm(title, message, okText = 'Подтвердить', cancelText = 'Отмена') {
            return new Promise((resolve) => {
                const modal = document.getElementById('confirm-modal');
                document.getElementById('confirm-modal-title').textContent = title;
                document.getElementById('confirm-modal-text').textContent = message;
                
                const btnOk = document.getElementById('confirm-btn-ok');
                const btnCancel = document.getElementById('confirm-btn-cancel');
                btnOk.textContent = okText;
                btnCancel.textContent = cancelText;

                const cleanup = () => {
                    modal.classList.remove('active');
                    btnOk.onclick = null;
                    btnCancel.onclick = null;
                };

                btnOk.onclick = () => { cleanup(); resolve(true); };
                btnCancel.onclick = () => { cleanup(); resolve(false); };
                modal.classList.add('active');
            });
        }

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

        function toggleLog(id) {
            logExpandedState[id] = !logExpandedState[id];
            const lb = document.getElementById('logbox-' + id);
            const btn = document.getElementById('log-toggle-btn-' + id);
            if (lb && btn) {
                if (logExpandedState[id]) {
                    lb.classList.add('expanded');
                    btn.textContent = 'Свернуть ▲';
                } else {
                    lb.classList.remove('expanded');
                    btn.textContent = 'Развернуть ▼';
                }
            }
        }

        async function handleBlurResume(id, fieldType) {
            const elId = fieldType + '-' + id;
            if (dirtyFields[elId]) {
                const item = (currentFullConfig.resumes || []).find(r => r.id === id);
                const fieldName = fieldType === 'name' ? 'название' : (fieldType === 'url' ? 'ссылку' : 'время');
                const targetTitle = item ? (item.name || id) : id;
                
                const confirmed = await showConfirm(
                    'Несохраненные изменения',
                    `Вы изменили ${fieldName} для "${targetTitle}". Сохранить изменения?`,
                    'Сохранить',
                    'Отменить'
                );

                if (confirmed) {
                    await saveResume(id);
                } else {
                    clearDirty(elId);
                    await loadData();
                }
            }
        }

        async function handleBlurAuth(fieldId) {
            if (dirtyFields[fieldId]) {
                const confirmed = await showConfirm(
                    'Сохранение авторизации',
                    'Вы изменили учетные данные HH.ru. Сохранить изменения?',
                    'Сохранить',
                    'Отменить'
                );

                if (confirmed) {
                    await saveAuth();
                } else {
                    clearDirty(fieldId);
                    await loadData();
                }
            }
        }

        async function manualRefresh() {
            await loadData();
            showToast('Статус синхронизирован с сервером', 'info');
        }

        async function loadData() {
            try {
                const res = await fetch('/api/config');
                if (res.status === 401) {
                    location.reload();
                    return;
                }
                currentFullConfig = await res.json();
                
                // Header status tags
                const tgTag = document.getElementById('tg-status-tag');
                if (tgTag) {
                    const tgOk = currentFullConfig.system?.telegram_enabled;
                    tgTag.className = 'tag-badge ' + (tgOk ? 'active' : '');
                    tgTag.textContent = tgOk ? `🤖 Telegram: активен (${currentFullConfig.system?.telegram_chat_id})` : '🤖 Telegram: не настроен';
                }

                const sessTag = document.getElementById('session-status-tag');
                if (sessTag) {
                    const hasAuth = Boolean(currentFullConfig.auth?.email);
                    sessTag.className = 'tag-badge ' + (hasAuth ? 'active' : '');
                    sessTag.textContent = hasAuth ? '🔐 Учетная запись: задана' : '⚠️ Учетная запись: отсутствует';
                }

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

            // Empty state check
            if (!resumesList || resumesList.length === 0) {
                container.innerHTML = `
                    <div class="empty-card">
                        <div style="font-size:36px;">📭</div>
                        <h3 style="margin:0; font-size:18px;">Список резюме пуст</h3>
                        <div style="color:var(--text-muted); font-size:13.5px; max-width:360px;">
                            Добавьте ссылку на ваше резюме на hh.ru и задайте интервал автоподъема.
                        </div>
                        <button class="btn btn-add" onclick="addNewResume()">➕ Добавить первое резюме</button>
                    </div>
                `;
                loadedResumeIds = [];
                return;
            }

            if (JSON.stringify(currentIds) !== JSON.stringify(loadedResumeIds)) {
                container.innerHTML = '';
                resumesList.forEach((item) => {
                    const isExpanded = Boolean(logExpandedState[item.id]);
                    const card = document.createElement('div');
                    card.className = 'card';
                    card.id = 'card-' + item.id;
                    card.innerHTML = `
                        <div class="card-header">
                            <input type="text" class="input-text" style="font-weight:700; font-size:15px; width:65%;" id="name-${item.id}" value="${item.name || ''}" placeholder="Название резюме" oninput="markDirty('name-${item.id}')" onblur="handleBlurResume('${item.id}', 'name')">
                            <div style="display:flex; align-items:center; gap:12px;">
                                <label class="switch" title="Включение/отключение расписания">
                                    <input type="checkbox" id="enable-${item.id}" onchange="toggleEnable('${item.id}')">
                                    <span class="slider"></span>
                                </label>
                                <button class="btn btn-delete" onclick="deleteResume('${item.id}')">🗑️ Удалить</button>
                            </div>
                        </div>

                        <div class="field-group">
                            <label class="field-label">Ссылка на резюме (HH URL)</label>
                            <input type="text" class="input-text" id="url-${item.id}" placeholder="https://hh.ru/resume/..." oninput="markDirty('url-${item.id}')" onblur="handleBlurResume('${item.id}', 'url')">
                        </div>

                        <div class="field-group">
                            <label class="field-label">Время автоподъема (ЧЧ:ММ MSK через запятую)</label>
                            <input type="text" class="input-text" id="sched-${item.id}" placeholder="07:00, 11:01, 15:02, 19:03, 23:04" oninput="markDirty('sched-${item.id}')" onblur="handleBlurResume('${item.id}', 'sched')">
                        </div>

                        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                            <span class="status-badge" id="badge-${item.id}"></span>
                            <div style="font-size:12px; color:var(--text-muted);" id="statustext-${item.id}"></div>
                        </div>

                        <div class="btn-group">
                            <button class="btn btn-save" onclick="saveResume('${item.id}')">💾 Сохранить</button>
                            <button class="btn btn-trigger" id="btn-trig-${item.id}" onclick="triggerResume('${item.id}')"></button>
                        </div>

                        <div class="field-group">
                            <div style="display:flex; justify-content:space-between; align-items:center;">
                                <label class="field-label" style="margin:0;">📜 Лог выполнения</label>
                                <button type="button" class="btn-toggle-log" id="log-toggle-btn-${item.id}" onclick="toggleLog('${item.id}')">${isExpanded ? 'Свернуть ▲' : 'Развернуть ▼'}</button>
                            </div>
                            <div class="log-box ${isExpanded ? 'expanded' : ''}" id="logbox-${item.id}"></div>
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
            if (!email) {
                showToast('Пожалуйста, укажите Email аккаунта', 'error');
                return;
            }
            currentFullConfig.auth = { email, password };
            clearDirty('auth-email');
            clearDirty('auth-password');
            await saveConfigToServer();
            showToast('Учетные данные HH.ru сохранены!', 'success');
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
            if (!currentFullConfig.resumes) currentFullConfig.resumes = [];
            currentFullConfig.resumes.push(newResume);
            await saveConfigToServer();
            showToast('Новое резюме добавлено в список', 'info');
        }

        async function deleteResume(id) {
            const item = (currentFullConfig.resumes || []).find(r => r.id === id);
            const name = item ? item.name : id;
            const confirmed = await showConfirm(
                'Удаление резюме',
                `Вы уверены, что хотите удалить "${name}"? Расписание автоподъема будет остановлено.`,
                'Удалить',
                'Отмена'
            );
            if (confirmed) {
                currentFullConfig.resumes = currentFullConfig.resumes.filter(r => r.id !== id);
                await saveConfigToServer();
                showToast(`Резюме "${name}" удалено`, 'info');
            }
        }

        async function toggleEnable(id) {
            const item = (currentFullConfig.resumes || []).find(r => r.id === id);
            if (item) {
                item.enabled = document.getElementById('enable-' + id).checked;
                await saveConfigToServer();
                showToast(`Автоподъем ${item.enabled ? 'включен' : 'приостановлен'}`, 'info');
            }
        }

        async function saveResume(id) {
            const item = (currentFullConfig.resumes || []).find(r => r.id === id);
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
                showToast(`Настройки "${item.name}" сохранены!`, 'success');
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
                showToast('Ошибка при сохранении на сервер', 'error');
            }
        }

        async function triggerResume(id) {
            const btn = document.getElementById('btn-trig-' + id);
            if (btn) {
                btn.disabled = true;
                btn.textContent = '⏳ Запуск...';
            }

            try {
                const res = await fetch('/api/trigger', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ resume_id: id })
                });
                const data = await res.json();
                if (!data.success) {
                    showToast(data.message || 'Ошибка запуска автоподъема', 'error');
                } else {
                    showToast('Процесс автоподъема запущен!', 'info');
                }
                setTimeout(loadData, 1000);
            } catch (err) {
                showToast('Ошибка сети при запуске автоподъема', 'error');
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

    def require_auth(self) -> bool:
        if not is_authenticated(self.headers):
            self.send_response(401)
            self.send_cors_headers()
            self.send_header("WWW-Authenticate", 'Basic realm="HH Resume Control Center"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Unauthorized")
            return False
        return True

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if not self.require_auth():
            return

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
            if not is_valid_resume_id(resume_id):
                res = json.dumps({"success": False, "message": "Некорректный ID резюме"}).encode("utf-8")
            elif resume_id in running_jobs:
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
        if not self.require_auth():
            return

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

            # System status metadata
            tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
            cfg_data["system"] = {
                "telegram_enabled": bool(tg_token and tg_chat),
                "telegram_chat_id": tg_chat or "",
                "port": PORT
            }

            # Mask real password for safety in UI
            if cfg_data.get("auth", {}).get("password"):
                cfg_data["auth"]["password"] = "••••••••"
                cfg_data["auth"]["password_saved"] = True

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
