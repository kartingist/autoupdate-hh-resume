import fcntl
import os
import sys
import re
import json
import logging
from logging.handlers import RotatingFileHandler
import argparse
import urllib.request
import urllib.parse
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright, Page, BrowserContext, Browser

# --- Настройка логирования ---
logger = logging.getLogger(__name__)

BASE_DIR = os.environ.get("HH_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
SCREENSHOTS_DIR = os.path.join(LOGS_DIR, "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

AUTH_FILE = os.path.join(BASE_DIR, "hh_session.json")
CONFIG_FILE = os.path.join(BASE_DIR, "resumes_config.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")

def setup_logger(target_id=None):
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if target_id:
        log_file = os.path.join(LOGS_DIR, f"{target_id}.log")
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        try:
            os.chmod(log_file, 0o666)
        except Exception:
            pass

setup_logger()

# --- Конфигурация ---
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

def send_telegram_notification(message: str, error_level: bool = True):
    """Отправляет уведомление в Telegram через Bot API без внешних зависимостей."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        return

    notify_success = os.environ.get("TELEGRAM_NOTIFY_ON_SUCCESS", "false").lower() in ("true", "1", "yes")
    if not error_level and not notify_success:
        return

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "HH-AutoUpdater/2.0"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                logger.warning(f"Telegram API вернул код {resp.status}")
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление в Telegram: {e}")

def cleanup_old_screenshots(max_files: int = 30, max_age_days: int = 7):
    """Удаляет устаревшие скриншоты, предотвращая переполнение дискового пространства."""
    try:
        if not os.path.exists(SCREENSHOTS_DIR):
            return
        now = datetime.now()
        png_files = []
        for fname in os.listdir(SCREENSHOTS_DIR):
            if fname.endswith(".png"):
                fpath = os.path.join(SCREENSHOTS_DIR, fname)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                    png_files.append((fpath, mtime))
                except Exception:
                    pass

        # Удаление скриншотов старше max_age_days
        for fpath, mtime in png_files:
            if (now - mtime).days > max_age_days:
                try:
                    os.remove(fpath)
                except Exception:
                    pass

        # Удержание не более max_files самых свежих
        remaining = [f for f in png_files if os.path.exists(f[0])]
        remaining.sort(key=lambda x: x[1], reverse=True)
        for fpath, _ in remaining[max_files:]:
            try:
                os.remove(fpath)
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Ошибка при очистке старых скриншотов: {e}")

def load_resumes_config():
    """Загружает конфигурацию с автоматической миграцией в мульти-аккаунтный формат."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                # Миграция: если это список старого формата
                if isinstance(data, list):
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
                                "resumes": data
                            }
                        ]
                    }
                
                # Миграция: если это словарь старого формата с ключом "auth" и "resumes" без "accounts"
                if isinstance(data, dict) and "accounts" not in data:
                    return {
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
                return data
        except Exception as e:
            logger.error(f"Ошибка чтения конфига резюме: {e}")

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

def update_resume_status(resume_id: str, last_time: str, last_result: str):
    """Обновляет статус выполнения резюме в конфиге с защитой fcntl flock."""
    lock_cfg_path = os.path.join(BASE_DIR, "resumes_config.lock")
    lock_fd = None
    try:
        lock_fd = open(lock_cfg_path, "a+")
        try:
            os.chmod(lock_cfg_path, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        
        cfg_data = load_resumes_config()
        updated = False
        for acc in cfg_data.get("accounts", []):
            for c in acc.get("resumes", []):
                if c.get("id") == resume_id:
                    c["last_time"] = last_time
                    c["last_result"] = last_result
                    updated = True
                    break
            if updated:
                break

        if updated:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Статус {resume_id} успешно сохранен: {last_result}")
    except Exception as e:
        logger.error(f"Ошибка сохранения статуса {resume_id}: {e}")
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass

class HHAutomation:
    def __init__(self, playwright: Playwright, email: str, password: str, account_id: str = "acc_default", target_id: str = None):
        self.playwright = playwright
        self.email = email
        self.password = password
        self.account_id = account_id
        self.target_id = target_id
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        
        # Индивидуальный файл сессии для каждого аккаунта
        self.auth_file = os.path.join(BASE_DIR, f"hh_session_{account_id}.json")
        
        # Только для acc_default допускается разовый фолбэк на старый общий файл сессии
        if account_id == "acc_default" and not os.path.exists(self.auth_file):
            candidates = [
                os.path.join(BASE_DIR, "hh_session.json"),
                os.path.join(BASE_DIR, f"hh_session_{target_id}.json") if target_id else None
            ]
            for cand in candidates:
                if cand and os.path.exists(cand):
                    try:
                        import shutil
                        shutil.copy2(cand, self.auth_file)
                        logger.info(f"Инициализирован файл сессии {self.auth_file} из {cand}")
                        break
                    except Exception:
                        pass

    def launch_browser(self, headless: bool = True):
        logger.info("Запуск браузера Chromium с режимом Stealth...")
        self.browser = self.playwright.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

        target_auth = self.auth_file if os.path.exists(self.auth_file) else None

        if target_auth:
            logger.info(f"Загрузка сохраненной сессии из {target_auth}...")
            self.context = self.browser.new_context(
                storage_state=target_auth,
                user_agent=user_agent,
                viewport={"width": 1280, "height": 800},
                locale="ru-RU"
            )
        else:
            logger.warning(f"Файл сессии для аккаунта {self.account_id} не найден, будет создана новая сессия.")
            self.context = self.browser.new_context(
                user_agent=user_agent,
                viewport={"width": 1280, "height": 800},
                locale="ru-RU"
            )

        self.page = self.context.new_page()
        self.page.set_default_timeout(60000)
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def _save_screenshot(self, name: str):
        if self.page and not self.page.is_closed():
            try:
                filename = os.path.join(SCREENSHOTS_DIR, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                self.page.screenshot(path=filename, full_page=False, timeout=15000)
                logger.info(f"Скриншот сохранен: {filename}")
                cleanup_old_screenshots()
            except Exception as e:
                logger.warning(f"Не удалось сохранить скриншот {name}: {e}")

    def _close_overlays_and_banners(self):
        """Закрывает куки-баннеры, всплывающие окна и оверлеи."""
        if not self.page or self.page.is_closed():
            return
        banner_texts = ["Понятно", "Принять", "Закрыть", "Хорошо", "Согласен", "Принять все"]
        for text in banner_texts:
            try:
                btn = self.page.locator(f"button:has-text('{text}'), [role='button']:has-text('{text}')").first
                if btn.is_visible(timeout=1000):
                    btn.click(force=True, timeout=3000)
                    logger.info(f"Баннер/оверлей '{text}' закрыт.")
                    self.page.wait_for_timeout(500)
            except Exception:
                pass

    def is_logged_in(self, target_url: str) -> bool:
        logger.info(f"Проверка статуса авторизации ({target_url})...")
        for attempt in range(1, 4):
            try:
                self.page.goto(target_url, wait_until="commit", timeout=45000)
                self.page.locator("body").wait_for(state="attached", timeout=30000)
                self.page.wait_for_timeout(2000)
                self._close_overlays_and_banners()

                login_btn = self.page.locator('button:has-text("Войти"), a:has-text("Войти")').first
                if login_btn.is_visible(timeout=3000):
                    logger.info("Кнопка 'Войти' обнаружена. Сессия не активна.")
                    return False

                logger.info("Сессия валидна, авторизация подтверждена.")
                return True
            except Exception as e:
                err_str = str(e)
                logger.warning(f"Ошибка проверки авторизации (попытка {attempt}/3): {err_str}")
                if attempt < 3:
                    self.page.wait_for_timeout(5000)
        return False

    def perform_login(self, account_name: str = ""):
        logger.info(f"Начало процесса входа для {self.email} (аккаунт: {account_name or self.account_id})...")
        try:
            self.page.goto("https://hh.ru/account/login?role=applicant", wait_until="commit", timeout=45000)
            self.page.locator("body").wait_for(state="attached", timeout=30000)
            self.page.wait_for_timeout(2000)
            self._close_overlays_and_banners()

            # Шаг 1: Нажатие стартовой кнопки 'Войти' (если на экране выбора типа аккаунта)
            try:
                login_start = self.page.locator("button:has-text('Войти')").first
                if login_start.is_visible(timeout=3000):
                    login_start.click(force=True)
                    logger.info("Нажата стартовая кнопка 'Войти'.")
                    self.page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Стартовая кнопка 'Войти': {e}")

            # Шаг 2: Выбор радио/вкладки 'Почта' (новый интерфейс Magritte)
            try:
                email_tab = self.page.locator('[data-qa="credential-type-email"], label:has-text("Почта"), button:has-text("Почта")').first
                if email_tab.is_visible(timeout=3000):
                    email_tab.click(force=True)
                    logger.info("Выбран способ входа через почту.")
                    self.page.wait_for_timeout(1000)
            except Exception as e:
                logger.warning(f"Выбор типа входа 'Почта': {e}")

            # Шаг 3: Ввод Email
            email_input = self.page.locator('[data-qa="applicant-login-input-email"], input[name="login"], input[type="email"], input[type="text"]').first
            email_input.wait_for(state="visible", timeout=15000)
            email_input.fill(self.email)
            logger.info("Email успешно введен.")
            self.page.wait_for_timeout(1000)

            # Шаг 4: Нажатие 'Войти с паролем'
            try:
                pass_btn = self.page.locator("button:has-text('парол')").first
                if pass_btn.is_visible(timeout=3000):
                    pass_btn.click(force=True)
                    logger.info("Кнопка 'Войти с паролем' нажата.")
                    self.page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Переход к вводу пароля: {e}")

            # Шаг 5: Ввод пароля
            pass_input = self.page.locator('[data-qa="applicant-login-input-password"], input[type="password"]').first
            pass_input.wait_for(state="visible", timeout=15000)
            pass_input.fill(self.password)
            logger.info("Пароль успешно введен.")
            self.page.wait_for_timeout(1000)

            # Шаг 6: Отправка формы авторизации
            submit_btn = self.page.locator('button:has-text("Войти")').first
            submit_btn.click(force=True)
            logger.info("Нажата финишная кнопка 'Войти'. Ожидание завершения авторизации...")
            self.page.wait_for_timeout(6000)

            # Сохранение обновленной сессии в файл аккаунта
            self.context.storage_state(path=self.auth_file)
            logger.info(f"Авторизация прошла успешно. Сессия аккаунта сохранена в {self.auth_file}")

        except Exception as e:
            logger.error(f"Критическая ошибка при попытке логина: {e}")
            self._save_screenshot("login_failed")
            send_telegram_notification(
                f"⚠️ <b>[HH Auto-Updater] Сбой входа в профиль</b>\n"
                f"Профиль: <b>{account_name or self.account_id}</b>\n"
                f"Почта: <code>{self.email}</code>\n"
                f"Ошибка: <code>{str(e)[:250]}</code>\n"
                f"<i>Требуется проверка логина/пароля или ручное обновление куки в веб-панели.</i>",
                error_level=True
            )
            raise e

    def raise_resume(self, resume_config: dict) -> bool:
        """Переходит на страницу резюме и выполняет клик по кнопке поднятия."""
        url = resume_config.get("url")
        name = resume_config.get("name")
        logger.info(f"Переход на страницу {name}: {url}")

        for page_load_attempt in range(1, 3):
            try:
                self.page.goto(url, wait_until="commit", timeout=45000)
                self.page.locator("body").wait_for(state="attached", timeout=30000)
                self.page.wait_for_timeout(2000)
                self._close_overlays_and_banners()
                break
            except Exception as ple:
                logger.warning(f"Ошибка загрузки страницы {name} (попытка {page_load_attempt}): {ple}")
                if page_load_attempt == 2:
                    raise ple
                self.page.wait_for_timeout(3000)

        # Список селекторов для обнаружения кнопки 'Поднять в поиске'
        selectors = [
            "[data-qa*='resume-update']",
            "button:has-text('Поднять в поиске')",
            "[data-qa='resume-update-button']",
            "button[data-qa*='resume-update']",
            "button:has-text('поднять')",
            "span:has-text('Поднять в поиске')",
            "div[data-qa='resume-update']",
            "[data-qa*='update-button']",
            "button[class*='update']"
        ]

        button = None
        for sel in selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.is_visible(timeout=1500):
                    button = loc
                    logger.info(f"Кнопка 'Поднять в поиске' найдена по селектору: {sel}")
                    break
            except Exception:
                continue

        if button:
            logger.info(f"Кнопка 'Поднять в поиске' доступна для {name}. Нажимаю реальным кликом с ожиданием ответа...")
            try:
                with self.page.expect_response(lambda r: "resume" in r.url and r.request.method == "POST", timeout=10000):
                    button.click(force=True, timeout=5000)
                logger.info("Сетевой ответ на клик кнопки поднятия успешно получен.")
            except Exception:
                # Если сетевой запрос не перехвачен, делаем фолбэк-клик
                button.click(force=True, timeout=5000)
            
            self.page.wait_for_timeout(3000)
            self._close_overlays_and_banners()

            # Проверка изменения интерфейса
            try:
                status_block = self.page.locator("[data-qa*='resume-update'], [data-qa='resume-status']").all_text_contents()
                if status_block:
                    logger.info(f"Статус блока поднятия после клика: {status_block}")
            except Exception:
                pass

            logger.info(f"РЕЗЮМЕ УСПЕШНО ПОДНЯТО: {name}!")
            return True
        else:
            logger.warning(f"Кнопка 'Поднять в поиске' не найдена для {name}. Возможно, время еще не пришло или резюме скрыто.")
            self._save_screenshot("button_not_available")
            return False

    def close(self):
        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            logger.info("Браузер закрыт, ресурсы освобождены.")
        except Exception as e:
            logger.warning(f"Ошибка при закрытии браузера: {e}")

def run_update_for_resume(target_id: str = None, max_retries: int = 3):
    """
    Запускает процедуру автоподъема для конкретного резюме (или всех активных).
    Изолирует сессии по account_id и блокирует параллельные запуски одного аккаунта.
    """
    cfg_data = load_resumes_config()
    accounts = cfg_data.get("accounts", [])
    if not accounts:
        logger.error("В конфигурации нет ни одного аккаунта.")
        return

    # Если передан target_id, ищем целевой аккаунт и резюме
    target_account = None
    target_resume = None

    if target_id:
        for acc in accounts:
            for r in acc.get("resumes", []):
                if r.get("id") == target_id:
                    target_account = acc
                    target_resume = r
                    break
            if target_account:
                break

        if not target_account or not target_resume:
            logger.error(f"Резюме с ID '{target_id}' не найдено ни в одном профиле.")
            return

        if not target_account.get("enabled", True):
            logger.info(f"Профиль '{target_account.get('name')}' выключен. Пропуск.")
            return

        if not target_resume.get("enabled", True):
            logger.info(f"Резюме '{target_resume.get('name')}' выключено. Пропуск.")
            return

        jobs = [(target_account, [target_resume])]
    else:
        # Без target_id: обрабатываем все активные резюме активных аккаунтов
        jobs = []
        for acc in accounts:
            if not acc.get("enabled", True):
                continue
            active_resumes = [r for r in acc.get("resumes", []) if r.get("enabled", True)]
            if active_resumes:
                jobs.append((acc, active_resumes))

    for account, items_to_process in jobs:
        acc_id = account.get("id", "acc_default")
        acc_name = account.get("name", "Профиль")
        auth_data = account.get("auth", {})
        email = auth_data.get("email") or os.environ.get("HH_EMAIL")
        password = auth_data.get("password") or os.environ.get("HH_PASSWORD")

        if not email or not password:
            logger.error(f"Учетные данные для профиля '{acc_name}' ({acc_id}) не заданы!")
            continue

        # Блокировка процесса по аккаунту для предотвращения гонок сессии
        account_lock_path = os.path.join(LOGS_DIR, f"{acc_id}.lock")
        lock_file = None
        try:
            lock_file = open(account_lock_path, "w")
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.info(f"Блокировка аккаунта получена: {account_lock_path}")
        except (BlockingIOError, IOError):
            logger.warning(f"Аккаунт '{acc_name}' ({acc_id}) уже обрабатывается другим процессом. Пропуск.")
            if lock_file:
                try:
                    lock_file.close()
                except Exception:
                    pass
            continue

        try:
            with sync_playwright() as playwright:
                hh = HHAutomation(
                    playwright,
                    email,
                    password,
                    account_id=acc_id,
                    target_id=target_id
                )
                hh.launch_browser(headless=True)

                first_url = items_to_process[0].get("url") or "https://hh.ru"
                has_session = os.path.exists(hh.auth_file)
                if not has_session or not hh.is_logged_in(first_url):
                    hh.perform_login(account_name=acc_name)

                for item in items_to_process:
                    resume_id = item.get("id")
                    resume_name = item.get("name")

                    logger.info(f"=== НАЧАЛО ОБРАБОТКИ [{acc_name}] {resume_name} ({resume_id}) ===")
                    
                    success = False
                    last_error = None

                    for attempt in range(1, max_retries + 1):
                        try:
                            if attempt > 1:
                                logger.info(f"--- Повторная попытка {attempt}/{max_retries} для {resume_name} ---")
                                if hh.page.is_closed() or (hh.browser and not hh.browser.is_connected()):
                                    logger.warning("Браузер был закрыт, переинициализирую...")
                                    hh.close()
                                    hh.launch_browser(headless=True)
                                    if not hh.is_logged_in(first_url):
                                        hh.perform_login(account_name=acc_name)
                                else:
                                    hh.page.wait_for_timeout(5000)

                            success = hh.raise_resume(item)
                            if success:
                                break
                            else:
                                logger.info(f"Кнопка поднятия еще не доступна по времени (или резюме скрыто). Ретраи не требуются.")
                                break

                        except Exception as exc:
                            last_error = exc
                            logger.error(f"Попытка {attempt}/{max_retries} завершилась сбоем: {exc}")
                            if attempt < max_retries:
                                wait_sec = attempt * 15
                                logger.info(f"Ожидание {wait_sec} сек перед повторной попыткой {attempt + 1}/{max_retries}...")
                                try:
                                    if not hh.page.is_closed():
                                        hh.page.wait_for_timeout(wait_sec * 1000)
                                except Exception:
                                    pass

                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if success:
                        last_res_str = "УСПЕШНО ПОДНЯТО"
                        send_telegram_notification(
                            f"✅ <b>[HH Auto-Updater] Резюме поднято</b>\n"
                            f"Профиль: <b>{acc_name}</b>\n"
                            f"Резюме: <b>{resume_name}</b>\n"
                            f"Время: {now_str}",
                            error_level=False
                        )
                    elif last_error:
                        last_res_str = f"Ошибка ({str(last_error)[:40]})"
                        send_telegram_notification(
                            f"❌ <b>[HH Auto-Updater] Ошибка автоподъема</b>\n"
                            f"Профиль: <b>{acc_name}</b>\n"
                            f"Резюме: <b>{resume_name}</b> (<code>{resume_id}</code>)\n"
                            f"Ошибка: <code>{str(last_error)[:250]}</code>\n"
                            f"Время: {now_str}",
                            error_level=True
                        )
                    else:
                        last_res_str = "Кнопка недоступна"

                    update_resume_status(resume_id, now_str, last_res_str)
                    logger.info(f"=== ЗАВЕРШЕНО: [{acc_name}] {resume_name} (Результат: {last_res_str}) ===\n")

                hh.close()

        except Exception as e:
            logger.critical(f"Скрипт завершился с критической ошибкой для профиля {acc_name}: {e}")
            send_telegram_notification(
                f"🚨 <b>[HH Auto-Updater] Критический сбой</b>\n"
                f"Профиль: <b>{acc_name}</b>\n"
                f"Ошибка: <code>{str(e)[:250]}</code>",
                error_level=True
            )
        finally:
            if lock_file:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    lock_file.close()
                except Exception:
                    pass

def main():
    parser = argparse.ArgumentParser(description="HH Resume Auto-Updater")
    parser.add_argument("--resume-id", type=str, help="ID резюме для обновления")
    args = parser.parse_args()

    if args.resume_id:
        setup_logger(args.resume_id)

    run_update_for_resume(args.resume_id)

if __name__ == "__main__":
    main()
