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
import shutil

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
                        os.environ[k.strip()] = v.strip().strip('"\'')
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
        return True
    auth_header = headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        return user == DASHBOARD_USER and pwd == DASHBOARD_PASS
    except Exception:
        return False

def is_valid_id(val: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_-]+$", str(val)))

running_jobs = {} # {resume_id: status_msg}

def load_resumes_config():
    """Загружает конфиг с автоматической миграцией в мульти-аккаунтный формат."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                if isinstance(data, list):
                    data = {
                        "accounts": [
                            {
                                "id": "acc_default",
                                "name": "Основной профиль",
                                "auth": {
                                    "email": os.environ.get("HH_EMAIL", ""),
                                    "password": os.environ.get("HH_PASSWORD", "")
                                },
                                "enabled": True,
                                "resumes": data
                            }
                        ]
                    }

                elif isinstance(data, dict) and "accounts" not in data:
                    data = {
                        "accounts": [
                            {
                                "id": "acc_default",
                                "name": "Основной профиль",
                                "auth": data.get("auth", {
                                    "email": os.environ.get("HH_EMAIL", ""),
                                    "password": os.environ.get("HH_PASSWORD", "")
                                }),
                                "enabled": True,
                                "resumes": data.get("resumes", [])
                            }
                        ]
                    }

                accounts = data.get("accounts", [])
                if not accounts:
                    data["accounts"] = [
                        {
                            "id": "acc_default",
                            "name": "Основной профиль",
                            "auth": {
                                "email": os.environ.get("HH_EMAIL", ""),
                                "password": os.environ.get("HH_PASSWORD", "")
                            },
                            "enabled": True,
                            "resumes": []
                        }
                    ]

                acc_default_session = os.path.join(BASE_DIR, "hh_session_acc_default.json")
                if not os.path.exists(acc_default_session):
                    old_sess = os.path.join(BASE_DIR, "hh_session.json")
                    if os.path.exists(old_sess):
                        try:
                            shutil.copy2(old_sess, acc_default_session)
                        except Exception:
                            pass

                return data
        except Exception as e:
            print("Error loading config:", e)

    return {
        "accounts": [
            {
                "id": "acc_default",
                "name": "Основной профиль",
                "auth": {
                    "email": os.environ.get("HH_EMAIL", ""),
                    "password": os.environ.get("HH_PASSWORD", "")
                },
                "enabled": True,
                "resumes": []
            }
        ]
    }

def save_resumes_config(cfg_data):
    """Сохраняет конфигурацию с защитой flock и слиянием паролей и статусов."""
    lock_cfg_path = os.path.join(BASE_DIR, "resumes_config.lock")
    lock_fd = None
    try:
        lock_fd = open(lock_cfg_path, "a+")
        try:
            os.chmod(lock_cfg_path, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    disk_data = json.load(f)
                    
                    if isinstance(disk_data, list):
                        disk_accounts = [{"id": "acc_default", "auth": {}, "resumes": disk_data}]
                    elif isinstance(disk_data, dict) and "accounts" not in disk_data:
                        disk_accounts = [{"id": "acc_default", "auth": disk_data.get("auth", {}), "resumes": disk_data.get("resumes", [])}]
                    elif isinstance(disk_data, dict):
                        disk_accounts = disk_data.get("accounts", [])
                    else:
                        disk_accounts = []

                    disk_acc_map = {a.get("id"): a for a in disk_accounts if isinstance(a, dict) and "id" in a}

                    for acc in cfg_data.get("accounts", []):
                        acc_id = acc.get("id")
                        disk_acc = disk_acc_map.get(acc_id)
                        if disk_acc:
                            submitted_pwd = acc.get("auth", {}).get("password", "").strip()
                            disk_pwd = disk_acc.get("auth", {}).get("password", "")
                            if not submitted_pwd or submitted_pwd in ("••••••••", "********"):
                                if "auth" not in acc:
                                    acc["auth"] = {}
                                acc["auth"]["password"] = disk_pwd

                            disk_resumes = {r["id"]: r for r in disk_acc.get("resumes", []) if isinstance(r, dict) and "id" in r}
                            for r in acc.get("resumes", []):
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
    """Синхронизирует системный crontab со всеми активными резюме всех активных аккаунтов."""
    accounts = cfg_data.get("accounts", [])
    cron_lines = []

    for acc in accounts:
        if not acc.get("enabled", True):
            continue
        resumes = acc.get("resumes", [])
        for item in resumes:
            if not item.get("enabled", True):
                continue
            resume_id = item.get("id")
            if not is_valid_id(resume_id):
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
        print("Crontab updated successfully for multi-accounts!")
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
    if not is_valid_id(resume_id):
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
    <title>HH Resume Multi-Account Control Center</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b1120;
            --card-bg: #1e293b;
            --card-bg-subtle: #172033;
            --card-bg-elevated: #243247;
            --accent-blue: #38bdf8;
            --accent-blue-hover: #0ea5e9;
            --accent-green: #10b981;
            --accent-green-hover: #059669;
            --accent-red: #ef4444;
            --accent-red-hover: #dc2626;
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
            padding: 24px 16px;
            min-height: 100vh;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        
        *:focus-visible {
            outline: 2px solid var(--accent-blue);
            outline-offset: 2px;
        }

        /* Хедер */
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-color);
        }
        .header-title {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .header-title h1 {
            font-size: 22px;
            font-weight: 800;
            margin: 0;
            letter-spacing: -0.02em;
            background: linear-gradient(135deg, #38bdf8 0%, #818cf8 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .header-badges {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }
        .sys-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px;
            border-radius: 9999px;
            font-size: 12px;
            font-weight: 600;
            background: rgba(30, 41, 59, 0.8);
            border: 1px solid var(--border-color);
            color: var(--text-muted);
        }
        .sys-badge.active {
            border-color: rgba(16, 185, 129, 0.4);
            color: #34d399;
            background: rgba(16, 185, 129, 0.1);
        }

        /* Панель вкладок аккаунтов */
        .tabs-wrapper {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 18px;
            flex-wrap: wrap;
        }
        .account-tabs {
            display: flex;
            align-items: center;
            gap: 8px;
            overflow-x: auto;
            padding-bottom: 4px;
            scrollbar-width: thin;
        }
        .tab-btn {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 9px 16px;
            background: var(--card-bg-subtle);
            color: var(--text-muted);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        .tab-btn:hover {
            background: var(--card-bg);
            color: var(--text-main);
            border-color: #475569;
        }
        .tab-btn.active {
            background: #1e293b;
            color: #38bdf8;
            border-color: #38bdf8;
            box-shadow: 0 4px 14px rgba(56, 189, 248, 0.15);
        }
        .tab-badge {
            background: rgba(255, 255, 255, 0.08);
            padding: 2px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 700;
        }
        .tab-btn.active .tab-badge {
            background: rgba(56, 189, 248, 0.2);
            color: #38bdf8;
        }
        .tab-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #64748b;
        }
        .tab-dot.online { background: #10b981; }

        .btn-add-account {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 9px 16px;
            background: rgba(56, 189, 248, 0.12);
            color: #38bdf8;
            border: 1px dashed rgba(56, 189, 248, 0.4);
            border-radius: 12px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        .btn-add-account:hover {
            background: rgba(56, 189, 248, 0.2);
            border-color: #38bdf8;
        }

        /* Карточка профиля */
        .profile-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 18px 22px;
            margin-bottom: 24px;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25);
            transition: all 0.2s ease;
        }
        .profile-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 14px;
        }
        .profile-title-group {
            display: flex;
            align-items: center;
            gap: 12px;
            flex: 1;
            min-width: 260px;
        }
        .profile-title-input {
            background: var(--card-bg-subtle);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 7px 12px;
            color: var(--text-main);
            font-size: 16px;
            font-weight: 700;
            width: 100%;
            max-width: 340px;
        }
        .profile-title-input:focus {
            border-color: var(--border-color-focus);
            outline: none;
        }
        .profile-actions {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }

        /* Сворачиваемая секция настроек профиля */
        .profile-settings-body {
            overflow: hidden;
            transition: max-height 0.35s ease, opacity 0.25s ease, margin-top 0.25s ease;
            max-height: 1200px;
            opacity: 1;
            margin-top: 18px;
            padding-top: 18px;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }
        .profile-settings-body.collapsed {
            max-height: 0;
            opacity: 0;
            margin-top: 0;
            padding-top: 0;
            border-top: none;
            pointer-events: none;
        }

        .profile-form-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
            margin-bottom: 16px;
        }
        .form-group {
            display: flex;
            flex-direction: column;
            gap: 6px;
        }
        .form-label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
        }
        .form-input {
            background: var(--card-bg-subtle);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 9px 12px;
            color: var(--text-main);
            font-size: 14px;
            transition: border-color 0.2s;
        }
        .form-input:focus {
            border-color: var(--border-color-focus);
            outline: none;
        }
        .pass-wrapper {
            position: relative;
            display: flex;
        }
        .pass-wrapper .form-input {
            width: 100%;
            padding-right: 44px;
        }
        .pass-toggle {
            position: absolute;
            right: 0;
            top: 0;
            bottom: 0;
            width: 44px;
            background: transparent;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .pass-toggle:hover { color: var(--text-main); }

        /* Переключатель Toggle Switch */
        .switch-container {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            user-select: none;
        }
        .switch-input { display: none; }
        .switch-track {
            width: 40px;
            height: 22px;
            background: #334155;
            border-radius: 9999px;
            position: relative;
            transition: background-color 0.2s;
        }
        .switch-thumb {
            width: 16px;
            height: 16px;
            background: #ffffff;
            border-radius: 50%;
            position: absolute;
            top: 3px;
            left: 3px;
            transition: transform 0.2s;
        }
        .switch-input:checked + .switch-track {
            background: var(--accent-green);
        }
        .switch-input:checked + .switch-track .switch-thumb {
            transform: translateX(18px);
        }
        .switch-label {
            font-size: 13px;
            font-weight: 600;
        }

        /* Кнопки */
        .btn {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            padding: 9px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }
        .btn-primary {
            background: var(--accent-blue);
            color: #0b1120;
        }
        .btn-primary:hover { background: var(--accent-blue-hover); transform: translateY(-1px); }
        .btn-danger {
            background: rgba(239, 68, 68, 0.15);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }
        .btn-danger:hover {
            background: rgba(239, 68, 68, 0.25);
            border-color: #ef4444;
        }
        .btn-secondary {
            background: var(--card-bg-subtle);
            color: var(--text-main);
            border: 1px solid var(--border-color);
        }
        .btn-secondary:hover { background: var(--card-bg-elevated); }
        .btn-sm {
            padding: 6px 12px;
            font-size: 12px;
            border-radius: 6px;
        }

        /* Аккордеон сессии */
        .accordion {
            margin-top: 12px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
            background: var(--card-bg-subtle);
        }
        .accordion-header {
            padding: 10px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            color: var(--text-muted);
        }
        .accordion-header:hover { color: var(--text-main); }
        .accordion-content {
            padding: 14px;
            border-top: 1px solid var(--border-color);
            display: none;
        }
        .accordion-content.open { display: block; }
        .session-textarea {
            width: 100%;
            height: 80px;
            background: #0f172a;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            padding: 10px;
            color: #cbd5e1;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            resize: vertical;
            margin-bottom: 10px;
        }

        /* Секция Резюме */
        .resumes-section {
            margin-top: 24px;
        }
        .resumes-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 18px;
            flex-wrap: wrap;
            gap: 12px;
        }
        .resumes-title {
            font-size: 18px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 0;
        }

        /* Адаптивная сетка резюме (до 2х в ширину) */
        .resumes-grid {
            display: grid;
            gap: 20px;
            width: 100%;
        }

        /* Режим 2+ резюме: ровно 2 колонки на экране > 880px, 1 колонка на смартфонах */
        .resumes-grid.mode-multi {
            grid-template-columns: repeat(auto-fit, minmax(440px, 1fr));
        }
        @media (min-width: 960px) {
            .resumes-grid.mode-multi {
                grid-template-columns: 1fr 1fr;
            }
        }
        @media (max-width: 768px) {
            .resumes-grid.mode-multi {
                grid-template-columns: 1fr;
            }
        }

        /* Режим 1 резюме: карточка-сплит (параметры слева, логи справа) */
        .resumes-grid.mode-single {
            grid-template-columns: 1fr;
        }

        .resume-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 14px;
            padding: 20px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        .resume-card:hover {
            border-color: #475569;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.25);
        }

        /* Сплит-карточка при 1 резюме */
        .resume-card-single-split {
            display: grid;
            grid-template-columns: minmax(360px, 1fr) minmax(420px, 1.25fr);
            gap: 24px;
            align-items: stretch;
        }
        @media (max-width: 900px) {
            .resume-card-single-split {
                grid-template-columns: 1fr;
            }
        }
        .resume-split-left {
            display: flex;
            flex-direction: column;
            gap: 14px;
        }
        .resume-split-right {
            display: flex;
            flex-direction: column;
            gap: 10px;
            background: #070c18;
            border: 1px solid var(--border-color);
            border-radius: 10px;
            padding: 14px;
            min-height: 280px;
        }
        .single-log-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding-bottom: 8px;
        }

        .resume-top {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
        }
        .resume-info { flex: 1; }
        .resume-name-input {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 6px;
            color: var(--text-main);
            font-size: 16px;
            font-weight: 700;
            width: 100%;
            padding: 4px 6px;
            margin-left: -6px;
        }
        .resume-name-input:focus {
            background: var(--card-bg-subtle);
            border-color: var(--border-color-focus);
            outline: none;
        }
        .resume-meta {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-top: 4px;
            font-size: 12px;
            color: var(--text-muted);
        }
        .resume-link {
            color: var(--accent-blue);
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }
        .resume-link:hover { text-decoration: underline; }

        /* Статус плашка */
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
        }
        .status-success {
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        .status-warning {
            background: rgba(245, 158, 11, 0.15);
            color: #fbbf24;
            border: 1px solid rgba(245, 158, 11, 0.3);
        }
        .status-error {
            background: rgba(239, 68, 68, 0.15);
            color: #f87171;
            border: 1px solid rgba(239, 68, 68, 0.3);
        }
        .status-running {
            background: rgba(56, 189, 248, 0.15);
            color: #38bdf8;
            border: 1px solid rgba(56, 189, 248, 0.3);
            animation: pulse 1.8s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        /* Расписание */
        .schedule-box {
            background: var(--card-bg-subtle);
            border: 1px solid rgba(255, 255, 255, 0.05);
            border-radius: 10px;
            padding: 12px;
        }
        .schedule-title {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }
        .schedule-chips {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 8px;
        }
        .time-chip {
            background: #1e293b;
            border: 1px solid var(--border-color);
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 12px;
            font-family: 'JetBrains Mono', monospace;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }
        .time-chip-del {
            cursor: pointer;
            color: var(--text-muted);
            border: none;
            background: transparent;
            padding: 0;
            font-size: 14px;
            line-height: 1;
        }
        .time-chip-del:hover { color: #f87171; }
        .schedule-add-row {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .time-input {
            width: 80px;
            background: #0f172a;
            border: 1px solid var(--border-color);
            color: var(--text-main);
            border-radius: 6px;
            padding: 4px 8px;
            font-size: 12px;
            font-family: 'JetBrains Mono', monospace;
        }
        .presets-row {
            display: flex;
            gap: 6px;
            margin-top: 6px;
        }
        .btn-preset {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-muted);
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            cursor: pointer;
        }
        .btn-preset:hover {
            color: var(--accent-blue);
            border-color: var(--accent-blue);
        }

        /* Действия с резюме */
        .resume-actions-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: auto;
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
        }

        /* Логи */
        .log-box {
            background: #070c18;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 12px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #94a3b8;
            max-height: 180px;
            overflow-y: auto;
            white-space: pre-wrap;
            word-break: break-all;
            display: none;
        }
        .log-box.open { display: block; }
        .single-log-box {
            display: block;
            flex: 1;
            max-height: none;
            min-height: 240px;
            border: none;
            padding: 6px 0;
            background: transparent;
        }

        /* Empty state */
        .empty-state {
            text-align: center;
            padding: 48px 24px;
            background: var(--card-bg-subtle);
            border: 1px dashed var(--border-color);
            border-radius: 16px;
            color: var(--text-muted);
        }
        .empty-icon { font-size: 40px; margin-bottom: 12px; }

        /* Modal Dialog */
        .modal-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.7);
            backdrop-filter: blur(4px);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal-overlay.open { display: flex; }
        .modal-card {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 24px;
            max-width: 440px;
            width: 90%;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
        }
        .modal-title { font-size: 18px; font-weight: 700; margin: 0 0 10px 0; }
        .modal-desc { font-size: 14px; color: var(--text-muted); margin: 0 0 20px 0; line-height: 1.5; }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; }

        /* Toast notifications */
        .toast-container {
            position: fixed;
            top: 20px;
            right: 20px;
            display: flex;
            flex-direction: column;
            gap: 10px;
            z-index: 2000;
        }
        .toast {
            background: var(--card-bg);
            border: 1px solid var(--border-color);
            padding: 12px 18px;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            box-shadow: 0 10px 25px rgba(0, 0, 0, 0.4);
            display: flex;
            align-items: center;
            gap: 10px;
            animation: slideIn 0.3s ease;
        }
        .toast-success { border-color: rgba(16, 185, 129, 0.4); color: #34d399; }
        .toast-error { border-color: rgba(239, 68, 68, 0.4); color: #f87171; }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Хедер -->
        <header class="header">
            <div class="header-title">
                <h1>⚡ HH Resume Multi-Account</h1>
            </div>
            <div class="header-badges">
                <div class="sys-badge" id="tg-badge">
                    <span>📱 Telegram</span>
                    <span id="tg-status-text">Проверка...</span>
                </div>
                <div class="sys-badge" id="cron-badge">
                    <span>⏱️ Crontab</span>
                    <span id="cron-status-text">Активен</span>
                </div>
                <div class="sys-badge">
                    <span>🔒 Basic Auth</span>
                    <span>Включен</span>
                </div>
            </div>
        </header>

        <!-- Вкладки аккаунтов -->
        <div class="tabs-wrapper">
            <div class="account-tabs" id="account-tabs-list">
                <!-- Рендерится динамически -->
            </div>
            <button class="btn-add-account" onclick="promptCreateAccount()">
                <span>➕ Новый профиль</span>
            </button>
        </div>

        <!-- Карточка активного профиля -->
        <section class="profile-card" id="profile-container">
            <div class="profile-header">
                <div class="profile-title-group">
                    <input type="text" id="cur-acc-name" class="profile-title-input" placeholder="Название профиля" onchange="markDirty()">
                </div>
                <div class="profile-actions">
                    <label class="switch-container">
                        <input type="checkbox" id="cur-acc-enabled" class="switch-input" onchange="markDirty()">
                        <div class="switch-track"><div class="switch-thumb"></div></div>
                        <span class="switch-label">Включен</span>
                    </label>
                    <button class="btn btn-secondary btn-sm" id="btn-toggle-profile-settings" onclick="toggleProfileSettings()">
                        <span id="profile-settings-btn-text">⚙️ Настройки профиля ▼</span>
                    </button>
                    <button class="btn btn-danger btn-sm" id="btn-delete-profile" onclick="confirmDeleteCurrentAccount()" title="Удалить профиль">
                        🗑️
                    </button>
                </div>
            </div>

            <!-- Сворачиваемое тело параметров профиля -->
            <div class="profile-settings-body" id="profile-settings-body">
                <div class="profile-form-grid">
                    <div class="form-group">
                        <label class="form-label">Email на HeadHunter</label>
                        <input type="email" id="cur-acc-email" class="form-input" placeholder="user@domain.com" onchange="markDirty()">
                    </div>
                    <div class="form-group">
                        <label class="form-label">
                            <span>Пароль на HeadHunter</span>
                            <span id="cur-acc-pwd-badge" style="color: #10b981; font-size: 11px;"></span>
                        </label>
                        <div class="pass-wrapper">
                            <input type="password" id="cur-acc-password" class="form-input" placeholder="Новый пароль (или сохраненный)" onchange="markDirty()">
                            <button type="button" class="pass-toggle" onclick="togglePasswordVisibility()">👁️</button>
                        </div>
                    </div>
                </div>

                <div style="display: flex; gap: 12px; align-items: center; flex-wrap: wrap;">
                    <button class="btn btn-primary" onclick="saveCurrentConfig()">
                        💾 Сохранить настройки профиля
                    </button>
                </div>

                <!-- Блок ручной вставки куки для текущего аккаунта -->
                <div class="accordion" id="session-accordion">
                    <div class="accordion-header" onclick="toggleAccordion('session-accordion')">
                        <span>🔑 Ручное обновление сессии / Cookies для этого профиля</span>
                        <span id="accordion-arrow">▼</span>
                    </div>
                    <div class="accordion-content">
                        <p style="font-size: 12px; color: var(--text-muted); margin: 0 0 8px 0;">
                            Вставьте токен <code>hhtoken</code> или полный JSON сессии (playwright storageState):
                        </p>
                        <textarea id="session-input" class="session-textarea" placeholder="Вставьте токен или JSON сессии..."></textarea>
                        <button class="btn btn-secondary btn-sm" onclick="saveSessionForCurrentAccount()">
                            📥 Сохранить сессию для этого профиля
                        </button>
                    </div>
                </div>
            </div>
        </section>

        <!-- Секция Резюме текущего профиля -->
        <section class="resumes-section">
            <div class="resumes-header">
                <h2 class="resumes-title" id="resumes-section-title">
                    <span>📄 Резюме профиля</span>
                </h2>
                <button class="btn btn-primary btn-sm" onclick="addNewResume()">
                    ➕ Добавить резюме
                </button>
            </div>

            <div class="resumes-grid" id="resumes-grid">
                <!-- Карточки резюме рендерятся динамически -->
            </div>
        </section>
    </div>

    <!-- Модальное окно подтверждения -->
    <div class="modal-overlay" id="confirm-modal">
        <div class="modal-card">
            <h3 class="modal-title" id="modal-title">Подтверждение</h3>
            <p class="modal-desc" id="modal-desc">Вы действительно хотите выполнить это действие?</p>
            <div class="modal-actions">
                <button class="btn btn-secondary" onclick="closeModal()">Отмена</button>
                <button class="btn btn-danger" id="modal-confirm-btn">Подтвердить</button>
            </div>
        </div>
    </div>

    <!-- Тосты -->
    <div class="toast-container" id="toast-container"></div>

    <script>
        let configState = { accounts: [], system: {} };
        let activeAccountId = null;
        let isDirty = false;
        
        // Хранилище состояния раскрытых логов и позиции скролла
        const openLogsMap = {};
        const logScrollPosMap = {};

        // Запоминание свернутости параметров профиля в localStorage
        let profileSettingsCollapsed = localStorage.getItem('hh_profile_settings_collapsed') === 'true';

        function showToast(message, type = 'success') {
            const container = document.getElementById('toast-container');
            const toast = document.createElement('div');
            toast.className = `toast toast-${type}`;
            toast.innerHTML = `<span>${type === 'success' ? '✅' : '❌'}</span><span>${message}</span>`;
            container.appendChild(toast);
            setTimeout(() => {
                toast.style.opacity = '0';
                toast.style.transition = 'opacity 0.3s';
                setTimeout(() => toast.remove(), 300);
            }, 3500);
        }

        function markDirty() {
            isDirty = true;
        }

        function togglePasswordVisibility() {
            const pwd = document.getElementById('cur-acc-password');
            pwd.type = pwd.type === 'password' ? 'text' : 'password';
        }

        function toggleAccordion(id) {
            const acc = document.getElementById(id);
            const content = acc.querySelector('.accordion-content');
            content.classList.toggle('open');
            const arrow = acc.querySelector('#accordion-arrow');
            arrow.textContent = content.classList.contains('open') ? '▲' : '▼';
        }

        function toggleProfileSettings() {
            profileSettingsCollapsed = !profileSettingsCollapsed;
            localStorage.setItem('hh_profile_settings_collapsed', profileSettingsCollapsed);
            applyProfileSettingsVisibility();
        }

        function applyProfileSettingsVisibility() {
            const body = document.getElementById('profile-settings-body');
            const btnText = document.getElementById('profile-settings-btn-text');
            if (profileSettingsCollapsed) {
                body.classList.add('collapsed');
                btnText.textContent = '⚙️ Настройки профиля ▼';
            } else {
                body.classList.remove('collapsed');
                btnText.textContent = '⚙️ Свернуть настройки ▲';
            }
        }

        function openModal({ title, desc, onConfirm, isDanger = true }) {
            document.getElementById('modal-title').textContent = title;
            document.getElementById('modal-desc').textContent = desc;
            const btn = document.getElementById('modal-confirm-btn');
            btn.className = isDanger ? 'btn btn-danger' : 'btn btn-primary';
            btn.onclick = () => {
                closeModal();
                onConfirm();
            };
            document.getElementById('confirm-modal').classList.add('open');
        }

        function closeModal() {
            document.getElementById('confirm-modal').classList.remove('open');
        }

        async function loadData() {
            const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
            if (activeTag === 'input' || activeTag === 'textarea') {
                return;
            }

            // Запоминаем текущую позицию прокрутки логов перед перерисовкой
            const currentAcc = getCurrentAccount();
            if (currentAcc && currentAcc.resumes) {
                currentAcc.resumes.forEach(r => {
                    const el = document.getElementById(`log-${r.id}`);
                    if (el) logScrollPosMap[r.id] = el.scrollTop;
                });
            }

            try {
                const res = await fetch('/api/config');
                if (res.status === 401) {
                    location.reload();
                    return;
                }
                const data = await res.json();
                configState = data;

                const tgBadge = document.getElementById('tg-badge');
                const tgText = document.getElementById('tg-status-text');
                if (data.system && data.system.telegram_enabled) {
                    tgBadge.classList.add('active');
                    tgText.textContent = `ID: ${data.system.telegram_chat_id}`;
                } else {
                    tgBadge.classList.remove('active');
                    tgText.textContent = 'Отключен';
                }

                const accounts = configState.accounts || [];
                if (!accounts.some(a => a.id === activeAccountId)) {
                    activeAccountId = accounts.length > 0 ? accounts[0].id : null;
                }

                renderTabs();
                renderCurrentAccount();
                applyProfileSettingsVisibility();
            } catch (err) {
                console.error("Ошибка обновления данных:", err);
            }
        }

        function renderTabs() {
            const list = document.getElementById('account-tabs-list');
            list.innerHTML = '';
            const accounts = configState.accounts || [];

            accounts.forEach(acc => {
                const btn = document.createElement('button');
                btn.className = `tab-btn ${acc.id === activeAccountId ? 'active' : ''}`;
                btn.onclick = () => switchAccount(acc.id);

                const dot = document.createElement('span');
                dot.className = `tab-dot ${acc.enabled ? 'online' : ''}`;
                btn.appendChild(dot);

                const title = document.createElement('span');
                title.textContent = acc.name || 'Без названия';
                btn.appendChild(title);

                const countBadge = document.createElement('span');
                countBadge.className = 'tab-badge';
                countBadge.textContent = (acc.resumes || []).length;
                btn.appendChild(countBadge);

                list.appendChild(btn);
            });
        }

        function switchAccount(accId) {
            activeAccountId = accId;
            renderTabs();
            renderCurrentAccount();
        }

        function getCurrentAccount() {
            return (configState.accounts || []).find(a => a.id === activeAccountId);
        }

        function renderCurrentAccount() {
            const acc = getCurrentAccount();
            if (!acc) return;

            document.getElementById('cur-acc-name').value = acc.name || '';
            document.getElementById('cur-acc-enabled').checked = acc.enabled !== false;

            const auth = acc.auth || {};
            document.getElementById('cur-acc-email').value = auth.email || '';
            const pwdInput = document.getElementById('cur-acc-password');
            pwdInput.value = auth.password || '';

            const pwdBadge = document.getElementById('cur-acc-pwd-badge');
            if (auth.password_saved) {
                pwdBadge.textContent = '✓ Сохранен';
            } else {
                pwdBadge.textContent = '';
            }

            const delBtn = document.getElementById('btn-delete-profile');
            const totalAccs = (configState.accounts || []).length;
            if (totalAccs <= 1) {
                delBtn.disabled = true;
                delBtn.style.opacity = '0.5';
                delBtn.title = 'Нельзя удалить единственный профиль';
            } else {
                delBtn.disabled = false;
                delBtn.style.opacity = '1';
                delBtn.title = 'Удалить профиль';
            }

            renderResumes(acc);
        }

        function renderResumes(acc) {
            const grid = document.getElementById('resumes-grid');
            grid.innerHTML = '';
            const resumes = acc.resumes || [];
            document.getElementById('resumes-section-title').innerHTML = `<span>📄 Резюме профиля (${resumes.length})</span>`;

            if (resumes.length === 0) {
                grid.className = 'resumes-grid';
                grid.innerHTML = `
                    <div class="empty-state" style="grid-column: 1 / -1;">
                        <div class="empty-icon">📂</div>
                        <h3 style="margin: 0 0 8px 0; color: var(--text-main);">В этом профиле пока нет резюме</h3>
                        <p style="margin: 0 0 16px 0; font-size: 13px;">Нажмите кнопку ниже, чтобы настроить ваше резюме для автоподъема.</p>
                        <button class="btn btn-primary btn-sm" onclick="addNewResume()">➕ Добавить резюме</button>
                    </div>
                `;
                return;
            }

            // Если ровно 1 резюме: карточка-сплит (параметры слева, логи справа)
            if (resumes.length === 1) {
                grid.className = 'resumes-grid mode-single';
                const resume = resumes[0];
                const card = document.createElement('div');
                card.className = 'resume-card resume-card-single-split';

                let statusClass = 'status-warning';
                let statusText = resume.last_result || 'Ожидание';
                if (resume.is_running) {
                    statusClass = 'status-running';
                    statusText = '⏳ Выполняется...';
                } else if (statusText.includes('УСПЕШНО')) {
                    statusClass = 'status-success';
                } else if (statusText.includes('Ошибка') || statusText.includes('Сбой')) {
                    statusClass = 'status-error';
                }

                const scheduleHtml = (resume.schedule || []).map(t => `
                    <span class="time-chip">
                        <span>${t}</span>
                        <button class="time-chip-del" onclick="removeScheduleTime('${resume.id}', '${t}')">×</button>
                    </span>
                `).join('');

                card.innerHTML = `
                    <!-- Левая колонка: Параметры резюме -->
                    <div class="resume-split-left">
                        <div class="resume-top">
                            <div class="resume-info">
                                <input type="text" class="resume-name-input" value="${resume.name || 'Резюме'}" onchange="updateResumeName('${resume.id}', this.value)">
                                <div class="resume-meta">
                                    <span>ID: <code>${resume.id}</code></span>
                                    <span>•</span>
                                    <a href="${resume.url || '#'}" target="_blank" rel="noopener" class="resume-link">Открыть на HH ↗</a>
                                </div>
                            </div>
                            <label class="switch-container">
                                <input type="checkbox" class="switch-input" ${resume.enabled !== false ? 'checked' : ''} onchange="toggleResumeEnabled('${resume.id}', this.checked)">
                                <div class="switch-track"><div class="switch-thumb"></div></div>
                            </label>
                        </div>

                        <div class="form-group">
                            <label class="form-label" style="font-size: 11px;">Ссылка на резюме (URL)</label>
                            <input type="text" class="form-input" style="font-size: 12px; padding: 6px 10px;" value="${resume.url || ''}" onchange="updateResumeUrl('${resume.id}', this.value)">
                        </div>

                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                            <span class="status-badge ${statusClass}">${statusText}</span>
                            <span style="color: var(--text-muted);">${resume.last_time ? resume.last_time : 'Никогда'}</span>
                        </div>

                        <div class="schedule-box">
                            <div class="schedule-title">
                                <span>Расписание автоподъема</span>
                            </div>
                            <div class="schedule-chips">
                                ${scheduleHtml || '<span style="font-size: 11px; color: var(--text-muted);">Расписание не задано</span>'}
                            </div>
                            <div class="schedule-add-row">
                                <input type="time" class="time-input" id="time-input-${resume.id}">
                                <button class="btn btn-secondary btn-sm" onclick="addScheduleTime('${resume.id}')">➕</button>
                            </div>
                            <div class="presets-row">
                                <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 4)">Каждые 4ч</button>
                                <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 6)">Каждые 6ч</button>
                                <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 8)">Каждые 8ч</button>
                            </div>
                        </div>

                        <div class="resume-actions-row">
                            <button class="btn btn-primary btn-sm" onclick="triggerUpdate('${resume.id}')" ${resume.is_running ? 'disabled' : ''}>
                                ${resume.is_running ? '⏳ Запуск...' : '⚡ Поднять сейчас'}
                            </button>
                            <button class="btn btn-danger btn-sm" onclick="confirmDeleteResume('${resume.id}')" title="Удалить резюме">
                                🗑️
                            </button>
                        </div>
                    </div>

                    <!-- Правая колонка: Терминал логов во всю высоту -->
                    <div class="resume-split-right">
                        <div class="single-log-header">
                            <span>📋 Терминал логов автоподъема</span>
                            <span style="font-size: 11px; color: var(--text-muted);">Обновляется онлайн</span>
                        </div>
                        <div class="log-box single-log-box" id="log-${resume.id}">${escapeHtml(resume.log || 'Логи пока пусты.')}</div>
                    </div>
                `;

                grid.appendChild(card);
                
                // Восстановление прокрутки
                const logEl = document.getElementById(`log-${resume.id}`);
                if (logEl && logScrollPosMap[resume.id] !== undefined) {
                    logEl.scrollTop = logScrollPosMap[resume.id];
                }
                return;
            }

            // Если 2 и более резюме: 2 колонки, логи аккордеоном снизу карточки
            grid.className = 'resumes-grid mode-multi';

            resumes.forEach((resume) => {
                const card = document.createElement('div');
                card.className = 'resume-card';

                let statusClass = 'status-warning';
                let statusText = resume.last_result || 'Ожидание';
                if (resume.is_running) {
                    statusClass = 'status-running';
                    statusText = '⏳ Выполняется...';
                } else if (statusText.includes('УСПЕШНО')) {
                    statusClass = 'status-success';
                } else if (statusText.includes('Ошибка') || statusText.includes('Сбой')) {
                    statusClass = 'status-error';
                }

                const scheduleHtml = (resume.schedule || []).map(t => `
                    <span class="time-chip">
                        <span>${t}</span>
                        <button class="time-chip-del" onclick="removeScheduleTime('${resume.id}', '${t}')">×</button>
                    </span>
                `).join('');

                const isLogOpen = !!openLogsMap[resume.id];

                card.innerHTML = `
                    <div class="resume-top">
                        <div class="resume-info">
                            <input type="text" class="resume-name-input" value="${resume.name || 'Резюме'}" onchange="updateResumeName('${resume.id}', this.value)">
                            <div class="resume-meta">
                                <span>ID: <code>${resume.id}</code></span>
                                <span>•</span>
                                <a href="${resume.url || '#'}" target="_blank" rel="noopener" class="resume-link">Открыть на HH ↗</a>
                            </div>
                        </div>
                        <label class="switch-container">
                            <input type="checkbox" class="switch-input" ${resume.enabled !== false ? 'checked' : ''} onchange="toggleResumeEnabled('${resume.id}', this.checked)">
                            <div class="switch-track"><div class="switch-thumb"></div></div>
                        </label>
                    </div>

                    <div class="form-group">
                        <label class="form-label" style="font-size: 11px;">Ссылка на резюме (URL)</label>
                        <input type="text" class="form-input" style="font-size: 12px; padding: 6px 10px;" value="${resume.url || ''}" onchange="updateResumeUrl('${resume.id}', this.value)">
                    </div>

                    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
                        <span class="status-badge ${statusClass}">${statusText}</span>
                        <span style="color: var(--text-muted);">${resume.last_time ? resume.last_time : 'Никогда'}</span>
                    </div>

                    <div class="schedule-box">
                        <div class="schedule-title">
                            <span>Расписание автоподъема</span>
                        </div>
                        <div class="schedule-chips">
                            ${scheduleHtml || '<span style="font-size: 11px; color: var(--text-muted);">Расписание не задано</span>'}
                        </div>
                        <div class="schedule-add-row">
                            <input type="time" class="time-input" id="time-input-${resume.id}">
                            <button class="btn btn-secondary btn-sm" onclick="addScheduleTime('${resume.id}')">➕</button>
                        </div>
                        <div class="presets-row">
                            <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 4)">Каждые 4ч</button>
                            <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 6)">Каждые 6ч</button>
                            <button class="btn-preset" onclick="setSchedulePreset('${resume.id}', 8)">Каждые 8ч</button>
                        </div>
                    </div>

                    <div class="resume-actions-row">
                        <div style="display: flex; gap: 8px;">
                            <button class="btn btn-primary btn-sm" onclick="triggerUpdate('${resume.id}')" ${resume.is_running ? 'disabled' : ''}>
                                ${resume.is_running ? '⏳ Запуск...' : '⚡ Поднять'}
                            </button>
                            <button class="btn btn-secondary btn-sm" id="log-btn-${resume.id}" onclick="toggleLog('${resume.id}')">
                                ${isLogOpen ? '📋 Логи ▲' : '📋 Логи ▼'}
                            </button>
                        </div>
                        <button class="btn btn-danger btn-sm" onclick="confirmDeleteResume('${resume.id}')" title="Удалить резюме">
                            🗑️
                        </button>
                    </div>

                    <div class="log-box ${isLogOpen ? 'open' : ''}" id="log-${resume.id}">${escapeHtml(resume.log || 'Логи пока пусты.')}</div>
                `;

                grid.appendChild(card);

                const logEl = document.getElementById(`log-${resume.id}`);
                if (logEl && logScrollPosMap[resume.id] !== undefined) {
                    logEl.scrollTop = logScrollPosMap[resume.id];
                }
            });
        }

        function escapeHtml(text) {
            return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        }

        function toggleLog(resumeId) {
            openLogsMap[resumeId] = !openLogsMap[resumeId];
            const box = document.getElementById(`log-${resumeId}`);
            if (box) box.classList.toggle('open', openLogsMap[resumeId]);
            const btn = document.getElementById(`log-btn-${resumeId}`);
            if (btn) btn.innerHTML = openLogsMap[resumeId] ? '📋 Логи ▲' : '📋 Логи ▼';
        }

        function promptCreateAccount() {
            const accName = prompt("Введите название нового профиля HH.ru:", `Профиль ${(configState.accounts || []).length + 1}`);
            if (!accName || !accName.trim()) return;

            const newId = `acc_${Date.now()}`;
            const newAcc = {
                id: newId,
                name: accName.trim(),
                auth: { email: "", password: "" },
                enabled: true,
                resumes: []
            };

            if (!configState.accounts) configState.accounts = [];
            configState.accounts.push(newAcc);
            activeAccountId = newId;

            saveCurrentConfig();
        }

        function confirmDeleteCurrentAccount() {
            const acc = getCurrentAccount();
            if (!acc) return;

            if ((configState.accounts || []).length <= 1) {
                showToast("Нельзя удалить единственный профиль!", "error");
                return;
            }

            openModal({
                title: "Удалить профиль?",
                desc: `Вы уверены, что хотите удалить профиль "${acc.name}" и все связанные резюме (${(acc.resumes || []).length} шт.)?`,
                onConfirm: () => {
                    configState.accounts = configState.accounts.filter(a => a.id !== acc.id);
                    activeAccountId = configState.accounts[0].id;
                    saveCurrentConfig();
                }
            });
        }

        function addNewResume() {
            const acc = getCurrentAccount();
            if (!acc) return;

            const resumeName = prompt("Введите название нового резюме:", "Новое резюме");
            if (!resumeName || !resumeName.trim()) return;

            const newId = `resume_${Date.now()}`;
            const newResume = {
                id: newId,
                name: resumeName.trim(),
                url: "https://hh.ru/resume/...",
                enabled: true,
                schedule: ["08:00", "12:00", "16:00", "20:00"],
                last_time: "-",
                last_result: "Новое резюме"
            };

            if (!acc.resumes) acc.resumes = [];
            acc.resumes.push(newResume);
            saveCurrentConfig();
        }

        function confirmDeleteResume(resumeId) {
            const acc = getCurrentAccount();
            if (!acc) return;

            openModal({
                title: "Удалить резюме?",
                desc: `Удалить резюме ID: ${resumeId} из текущего профиля?`,
                onConfirm: () => {
                    acc.resumes = acc.resumes.filter(r => r.id !== resumeId);
                    saveCurrentConfig();
                }
            });
        }

        function updateResumeName(resumeId, newName) {
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r) {
                r.name = newName.trim();
                saveCurrentConfig();
            }
        }

        function updateResumeUrl(resumeId, newUrl) {
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r) {
                r.url = newUrl.trim();
                saveCurrentConfig();
            }
        }

        function toggleResumeEnabled(resumeId, enabled) {
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r) {
                r.enabled = enabled;
                saveCurrentConfig();
            }
        }

        function addScheduleTime(resumeId) {
            const input = document.getElementById(`time-input-${resumeId}`);
            if (!input || !input.value) return;

            const time = input.value.trim();
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r) {
                if (!r.schedule) r.schedule = [];
                if (!r.schedule.includes(time)) {
                    r.schedule.push(time);
                    r.schedule.sort();
                    saveCurrentConfig();
                }
            }
        }

        function removeScheduleTime(resumeId, time) {
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r && r.schedule) {
                r.schedule = r.schedule.filter(t => t !== time);
                saveCurrentConfig();
            }
        }

        function setSchedulePreset(resumeId, stepHours) {
            const acc = getCurrentAccount();
            if (!acc) return;
            const r = acc.resumes.find(x => x.id === resumeId);
            if (r) {
                const times = [];
                for (let h = 7; h < 24; h += stepHours) {
                    times.push(`${String(h).padStart(2, '0')}:10`);
                }
                r.schedule = times;
                saveCurrentConfig();
            }
        }

        async function saveCurrentConfig() {
            const acc = getCurrentAccount();
            if (acc) {
                acc.name = document.getElementById('cur-acc-name').value.trim() || acc.name;
                acc.enabled = document.getElementById('cur-acc-enabled').checked;
                if (!acc.auth) acc.auth = {};
                acc.auth.email = document.getElementById('cur-acc-email').value.trim();
                const pwdVal = document.getElementById('cur-acc-password').value.trim();
                if (pwdVal && pwdVal !== '••••••••' && pwdVal !== '********') {
                    acc.auth.password = pwdVal;
                }
            }

            try {
                const res = await fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(configState)
                });
                const result = await res.json();
                if (result.success) {
                    showToast('Конфигурация успешно сохранена!');
                    isDirty = false;
                    loadData();
                } else {
                    showToast('Ошибка сохранения конфигурации', 'error');
                }
            } catch (err) {
                showToast('Ошибка сети при сохранении', 'error');
            }
        }

        async function saveSessionForCurrentAccount() {
            const acc = getCurrentAccount();
            if (!acc) return;

            const text = document.getElementById('session-input').value.trim();
            if (!text) {
                showToast('Введите данные сессии или токен', 'error');
                return;
            }

            try {
                const res = await fetch('/api/session', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ account_id: acc.id, session: text })
                });
                const result = await res.json();
                if (result.success) {
                    showToast(`Сессия для профиля "${acc.name}" сохранена!`);
                    document.getElementById('session-input').value = '';
                } else {
                    showToast(result.message || 'Ошибка сохранения сессии', 'error');
                }
            } catch (err) {
                showToast('Ошибка сети при отправке сессии', 'error');
            }
        }

        async function triggerUpdate(resumeId) {
            try {
                const res = await fetch('/api/trigger', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ resume_id: resumeId })
                });
                const result = await res.json();
                if (result.success) {
                    showToast('Автоподъем резюме запущен!');
                    setTimeout(loadData, 1000);
                } else {
                    showToast(result.message || 'Не удалось запустить', 'error');
                }
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
            account_id = body.get("account_id", "acc_default")
            if not is_valid_id(account_id):
                account_id = "acc_default"

            session_data = body.get("session")
            if session_data:
                try:
                    session_file = os.path.join(BASE_DIR, f"hh_session_{account_id}.json")
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
                    res = json.dumps({"success": True, "message": f"Сессия аккаунта {account_id} успешно сохранена!"}).encode("utf-8")
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
            if not is_valid_id(resume_id):
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
            
            for acc in cfg_data.get("accounts", []):
                auth_data = acc.get("auth", {})
                real_pwd = auth_data.get("password", "")
                if real_pwd:
                    auth_data["password"] = "••••••••"
                    auth_data["password_saved"] = True
                else:
                    auth_data["password"] = ""
                    auth_data["password_saved"] = False

                for item in acc.get("resumes", []):
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

            tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
            cfg_data["system"] = {
                "telegram_enabled": bool(tg_token and tg_chat),
                "telegram_chat_id": tg_chat or "",
                "port": PORT
            }

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

def run_server():
    server_address = ('0.0.0.0', PORT)
    with ThreadingTCPServer(server_address, DashboardHandler) as httpd:
        print(f"HH Multi-Account Dashboard running at http://0.0.0.0:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
