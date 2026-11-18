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
        # Ротация логов: макс 5 МБ на файл, хранение до 3 ротированных архивов
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
        fh.setFormatter(formatter)
        logger.addHandler(fh)

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
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {"auth": {"email": "", "password": ""}, "resumes": data}
                return data
        except Exception as e:
            logger.error(f"Ошибка чтения конфига резюме: {e}")
    return {"auth": {"email": "", "password": ""}, "resumes": []}

def update_resume_status(resume_id: str, last_time: str, last_result: str):
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
        resumes = cfg_data.get("resumes", [])
        updated = False
        for c in resumes:
            if c.get("id") == resume_id:
                c["last_time"] = last_time
                c["last_result"] = last_result
                updated = True
                break
        if updated:
            cfg_data["resumes"] = resumes
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
    def __init__(self, playwright: Playwright, email: str, password: str, target_id: str = None):
        self.playwright = playwright
        self.email = email
        self.password = password
        self.target_id = target_id
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None
        
        # Isolated session file for target_id
        if target_id:
            self.auth_file = os.path.join(BASE_DIR, f"hh_session_{target_id}.json")
        else:
            self.auth_file = AUTH_FILE

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
            logger.warning("Файл сессии не найден, будет создана новая сессия.")
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

    def perform_login(self):
        logger.info(f"Начало процесса входа для {self.email}...")
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

            # Сохранение обновленной сессии
            self.context.storage_state(path=self.auth_file)
            try:
                self.context.storage_state(path=AUTH_FILE)
            except Exception:
                pass
            logger.info("Авторизация прошла успешно. Сессия обновлена и сохранена.")

        except Exception as e:
            logger.error(f"Критическая ошибка при попытке логина: {e}")
            self._save_screenshot("login_failed")
            send_telegram_notification(
                f"⚠️ <b>[HH Auto-Updater] Сбой авторизации</b>\n"
                f"Email: <code>{self.email}</code>\n"
                f"Ошибка: <code>{str(e)[:250]}</code>\n"
                f"<i>Проверьте актуальность учетных данных, 2FA или капчу на сервере.</i>",
                error_level=True
            )
            raise

    def raise_resume(self, resume_item: dict) -> bool:
        """
        Возвращает:
            True  - Резюме успешно поднято
            False - Кнопка еще не активна по времени (не является ошибкой, ретрай не нужен)
        Выбрасывает исключение при сбое (для механизма ретраев).
        """
        resume_url = resume_item.get("url", "").strip()
        resume_name = resume_item.get("name", "Резюме")
        
        if not resume_url or "hh.ru/resume/" not in resume_url:
            logger.error(f"Некорректный или отсутствующий URL для {resume_name}: '{resume_url}'")
            return False

        for attempt in range(1, 3):
            try:
                logger.info(f"Переход на страницу {resume_name} (попытка {attempt}): {resume_url}")
                if self.page.url.rstrip("/") != resume_url.rstrip("/"):
                    self.page.goto(resume_url, wait_until="commit", timeout=45000)
                    self.page.locator("body").wait_for(state="attached", timeout=30000)
                else:
                    logger.info(f"Уже на странице {resume_name}, обновляю страницу...")
                    self.page.reload(wait_until="commit", timeout=45000)
                    self.page.locator("body").wait_for(state="attached", timeout=30000)
                break
            except Exception as e:
                logger.warning(f"Ошибка загрузки страницы {resume_name} (попытка {attempt}): {e}")
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(3000)
        
        self.page.wait_for_timeout(3000)
        self._close_overlays_and_banners()

        # Приоритетные селекторы конкретных кнопок и ссылок (не обобщенных div'ов)
        button_selectors = [
            "button:has-text('Поднять в поиске')",
            "button[data-qa='resume-update-button']",
            "button[data-qa*='resume-update']",
            "button:has-text('Поднять')",
            "a:has-text('Поднять в поиске')",
            "a[data-qa='resume-update-button']",
            "a[data-qa*='resume-update']",
            "a:has-text('Поднять')",
            "[data-qa='resume-update-button']",
            "[data-qa*='resume-update']:not(div):not(section)"
        ]
        
        button = None
        for sel in button_selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=2000):
                    button = el
                    logger.info(f"Кнопка 'Поднять в поиске' найдена по селектору: {sel}")
                    break
            except Exception:
                pass

        if not button:
            try:
                el = self.page.locator("button, a, [role='button']").filter(
                    has_text=re.compile(r"Поднять\s+в\s+поиске", re.I)
                ).first
                if el.is_visible(timeout=2000):
                    button = el
                    logger.info("Кнопка 'Поднять в поиске' найдена по регулярному выражению кнопки.")
            except Exception:
                pass

        if not button:
            # Умное ожидание таймера 'Можно сегодня в HH:MM'
            try:
                page_text = self.page.locator("body").inner_text()
                time_match = re.search(r"Можно\s+(?:сегодня\s+)?в\s*(\d{1,2}):(\d{2})", page_text)
                if time_match:
                    h, m = int(time_match.group(1)), int(time_match.group(2))
                    now = datetime.now()
                    target_time = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    wait_sec = (target_time - now).total_seconds()
                    if 0 < wait_sec <= 420:  # ждем до 7 минут если время почти наступило
                        logger.info(f"Таймер HH: кнопка появится в {h:02d}:{m:02d} (через {int(wait_sec)} сек). Ожидаю...")
                        self.page.wait_for_timeout(int(wait_sec + 8) * 1000)
                        self.page.reload(wait_until="commit", timeout=45000)
                        self.page.locator("body").wait_for(state="attached", timeout=30000)
                        self.page.wait_for_timeout(3000)
                        self._close_overlays_and_banners()
                        for sel in button_selectors:
                            try:
                                el = self.page.locator(sel).first
                                if el.is_visible(timeout=3000):
                                    button = el
                                    logger.info(f"Кнопка 'Поднять в поиске' успешно найдена после ожидания таймера: {sel}")
                                    break
                            except Exception:
                                pass
            except Exception as wait_e:
                logger.warning(f"Ошибка при проверке таймера ожидания: {wait_e}")

        if button:
            try:
                logger.info(f"Кнопка 'Поднять в поиске' доступна для {resume_name}. Нажимаю реальным кликом с ожиданием ответа...")
                self._close_overlays_and_banners()
                button.scroll_into_view_if_needed(timeout=5000)
                self.page.wait_for_timeout(1000)
                
                # Клик с защитой от зависания и fallback на JS-клик
                click_succeeded = False
                try:
                    button.click(timeout=10000)
                    click_succeeded = True
                    logger.info("Клик через Playwright API успешно выполнен.")
                except Exception as click_err:
                    logger.warning(f"Playwright click вызвал ошибку ({click_err}), применяю JavaScript click...")
                    try:
                        button.evaluate("el => el.click()")
                        click_succeeded = True
                        logger.info("JavaScript click (element.click()) успешно выполнен.")
                    except Exception as js_err:
                        logger.warning(f"JavaScript click не удался ({js_err}), пробую force-клик...")
                        button.click(force=True, timeout=10000)
                        click_succeeded = True
                        logger.info("Force-клик успешно выполнен.")

                self.page.wait_for_timeout(5000)
                
                # Проверяем статус блока поднятия
                try:
                    time_info = self.page.locator("text=Поднятие резюме").first.locator("..").all_inner_texts()
                    logger.info(f"Статус блока поднятия после клика: {time_info}")
                except Exception:
                    pass

                # Валидация: изменилось ли состояние кнопки
                btn_still_clickable = False
                try:
                    if button.is_visible(timeout=2000):
                        btn_text = button.inner_text().strip().lower()
                        if "поднять" in btn_text:
                            btn_still_clickable = True
                except Exception:
                    btn_still_clickable = False

                if not btn_still_clickable:
                    logger.info(f"РЕЗЮМЕ УСПЕШНО ПОДНЯТО: {resume_name}!")
                    return True
                else:
                    logger.warning(f"Кнопка осталась активной со словом 'Поднять' после клика. Возможно, клик не прошел.")
                    raise RuntimeError("Кнопка поднятия осталась видимой и активной после клика.")

            except Exception as e:
                logger.error(f"Ошибка при клике на кнопку подъема: {e}")
                self._save_screenshot("button_click_failed")
                raise  # Выбрасываем для срабатывания ретрая
        else:
            logger.warning(f"Кнопка 'Поднять в поиске' не найдена для {resume_name}. Возможно, время еще не пришло или резюме скрыто.")
            self._save_screenshot("button_not_available")
            return False

    def close(self):
        try:
            if self.context: self.context.close()
        except Exception:
            pass
        try:
            if self.browser: self.browser.close()
        except Exception:
            pass
        logger.info("Браузер закрыт, ресурсы освобождены.")


def run_update_for_resume(target_id=None):
    setup_logger(target_id)
    cleanup_old_screenshots()

    lock_filename = os.path.join(LOGS_DIR, f"{target_id or 'all'}.lock")
    lock_file = None
    try:
        lock_file = open(lock_filename, "a+")
        try:
            os.chmod(lock_filename, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"Блокировка получена: {lock_filename}")
    except (BlockingIOError, IOError, OSError) as e:
        if isinstance(e, BlockingIOError) or (hasattr(e, 'errno') and e.errno in (11, 35)):
            logger.warning(f"Скрипт для target_id='{target_id}' уже выполняется. Выход.")
            if lock_file:
                lock_file.close()
            sys.exit(0)
        else:
            logger.warning(f"Не удалось получить файл блокировки {lock_filename} ({e}), продолжаю без блокировки.")

    cfg_data = load_resumes_config()
    auth_info = cfg_data.get("auth", {})
    email = auth_info.get("email") or os.environ.get("HH_EMAIL", "")
    password = auth_info.get("password") or os.environ.get("HH_PASSWORD", "")
    resumes_list = cfg_data.get("resumes", [])

    if not email or not password:
        logger.error("Логин и пароль HH.ru не заданы ни в .env, ни в конфигурации!")
        send_telegram_notification("⚠️ <b>[HH Auto-Updater] Ошибка конфигурации</b>\nЛогин и пароль не заданы ни в .env, ни в веб-панели!", error_level=True)
        return

    if not resumes_list:
        logger.error("Список резюме пуст.")
        return

    items_to_process = []
    for item in resumes_list:
        if target_id:
            if item.get("id") == target_id:
                items_to_process.append(item)
        else:
            if item.get("enabled", True):
                items_to_process.append(item)

    if not items_to_process:
        logger.warning(f"Нет активных резюме для обработки (target_id={target_id}).")
        return

    max_retries = 3

    with sync_playwright() as playwright:
        hh = HHAutomation(playwright, email=email, password=password, target_id=target_id)
        try:
            hh.launch_browser(headless=True)

            first_url = items_to_process[0].get("url") or "https://hh.ru"
            if not hh.is_logged_in(first_url):
                hh.perform_login()

            for item in items_to_process:
                resume_id = item.get("id")
                resume_name = item.get("name")

                logger.info(f"=== НАЧАЛО ОБРАБОТКИ {resume_name} ({resume_id}) ===")
                
                success = False
                last_error = None

                for attempt in range(1, max_retries + 1):
                    try:
                        if attempt > 1:
                            logger.info(f"--- Повторная попытка {attempt}/{max_retries} для {resume_name} ---")
                            # Проверяем, жив ли браузер и страница
                            if hh.page.is_closed() or (hh.browser and not hh.browser.is_connected()):
                                logger.warning("Браузер был закрыт, переинициализирую...")
                                hh.close()
                                hh.launch_browser(headless=True)
                                if not hh.is_logged_in(first_url):
                                    hh.perform_login()
                            else:
                                hh.page.wait_for_timeout(5000)

                        success = hh.raise_resume(item)
                        if success:
                            break
                        else:
                            # Если вернулся False без исключения - кнопка не найдена/еще не наступило время
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
                        f"Резюме: <b>{resume_name}</b>\n"
                        f"Время: {now_str}",
                        error_level=False
                    )
                elif last_error:
                    last_res_str = f"Ошибка ({str(last_error)[:40]})"
                    send_telegram_notification(
                        f"❌ <b>[HH Auto-Updater] Ошибка автоподъема</b>\n"
                        f"Резюме: <b>{resume_name}</b> (<code>{resume_id}</code>)\n"
                        f"Ошибка: <code>{str(last_error)[:250]}</code>\n"
                        f"Время: {now_str}",
                        error_level=True
                    )
                else:
                    last_res_str = "Кнопка недоступна"

                update_resume_status(resume_id, now_str, last_res_str)
                logger.info(f"=== ЗАВЕРШЕНО: {resume_name} (Результат: {last_res_str}) ===\n")

        except Exception as e:
            logger.critical(f"Скрипт завершился с критической ошибкой: {e}")
            send_telegram_notification(
                f"🚨 <b>[HH Auto-Updater] Критический сбой скрипта</b>\n"
                f"Ошибка: <code>{str(e)[:250]}</code>",
                error_level=True
            )
        finally:
            hh.close()
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

    run_update_for_resume(args.resume_id)

if __name__ == "__main__":
    main()
