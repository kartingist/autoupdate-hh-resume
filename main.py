import fcntl
import os
import sys
import re
import json
import logging
import argparse
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
        fh = logging.FileHandler(log_file, encoding='utf-8')
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
        if self.page:
            try:
                filename = os.path.join(SCREENSHOTS_DIR, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
                self.page.screenshot(path=filename, full_page=False, timeout=15000)
                logger.info(f"Скриншот сохранен: {filename}")
            except Exception as e:
                logger.warning(f"Не удалось сохранить скриншот {name}: {e}")

    def is_logged_in(self, target_url: str) -> bool:
        logger.info(f"Проверка статуса авторизации ({target_url})...")
        for attempt in range(1, 4):
            try:
                self.page.goto(target_url, wait_until="commit", timeout=45000)
                self.page.locator("body").wait_for(state="attached", timeout=30000)
                self.page.wait_for_timeout(2000)

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
            raise

    def raise_resume(self, resume_item: dict) -> bool:
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
                    logger.info(f"Уже на странице {resume_name}, пропускаю повторную загрузку.")
                break
            except Exception as e:
                logger.warning(f"Ошибка загрузки страницы {resume_name} (попытка {attempt}): {e}")
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(3000)
        self.page.wait_for_timeout(3000)
        
        # Закрываем баннеры согласия с cookies если есть
        for cookie_text in ["Понятно", "Принять", "Закрыть"]:
            try:
                cookie_close = self.page.locator(f"button:has-text('{cookie_text}')").first
                if cookie_close.is_visible(timeout=1000):
                    cookie_close.click(force=True)
                    logger.info(f"Баннер '{cookie_text}' закрыт.")
            except Exception:
                pass

        # Ищем кнопку поднятия резюме
        button_selectors = [
            "[data-qa*='resume-update']",
            "button:has-text('Поднять в поиске')",
            "button:has-text('Поднять')",
            "a:has-text('Поднять в поиске')",
            "a:has-text('Поднять')",
            "[data-qa='resume-update-button']"
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
                el = self.page.get_by_text(re.compile(r"Поднять\s+в\s+поиске", re.I)).first
                if el.is_visible(timeout=2000):
                    button = el
                    logger.info("Кнопка 'Поднять в поиске' найдена по регулярному выражению.")
            except Exception:
                pass

        if button:
            try:
                logger.info(f"Кнопка 'Поднять в поиске' доступна для {resume_name}. Нажимаю реальным кликом с ожиданием ответа...")
                button.scroll_into_view_if_needed()
                self.page.wait_for_timeout(1000)
                
                try:
                    with self.page.expect_response(lambda res: "touch" in res.url or "resume" in res.url, timeout=15000):
                        button.click()
                except Exception as net_e:
                    logger.warning(f"Ожидание сетевого ответа: {net_e}. Повторяю клик с force...")
                    button.click(force=True)

                self.page.wait_for_timeout(5000)
                
                # Проверяем статус блока поднятия
                try:
                    time_info = self.page.locator("text=Поднятие резюме").first.locator("..").all_inner_texts()
                    logger.info(f"Статус блока поднятия после клика: {time_info}")
                except Exception:
                    pass

                logger.info(f"РЕЗЮМЕ УСПЕШНО ПОДНЯТО: {resume_name}!")
                return True
            except Exception as e:
                logger.error(f"Ошибка при клике на кнопку подъема: {e}")
                self._save_screenshot("button_click_failed")
                return False
        else:
            logger.warning(f"Кнопка 'Поднять в поиске' не найдена для {resume_name}. Возможно, время еще не пришло или резюме скрыто.")
            self._save_screenshot("button_not_available")
            return False

    def close(self):
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        logger.info("Браузер закрыт, ресурсы освобождены.")


def run_update_for_resume(target_id=None):
    setup_logger(target_id)

    lock_filename = f"/tmp/hh_autoupdate_{target_id or 'all'}.lock"
    lock_file = None
    try:
        fd = os.open(lock_filename, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o666)
        lock_file = os.fdopen(fd, 'a+')
        try:
            os.chmod(lock_filename, 0o666)
        except Exception:
            pass
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info(f"Блокировка получена: {lock_filename}")
    except (IOError, OSError) as e:
        logger.warning(f"Скрипт для target_id='{target_id}' уже выполняется. Выход. ({e})")
        if lock_file:
            lock_file.close()
        sys.exit(0)

    cfg_data = load_resumes_config()
    auth_info = cfg_data.get("auth", {})
    email = auth_info.get("email") or os.environ.get("HH_EMAIL", "")
    password = auth_info.get("password") or os.environ.get("HH_PASSWORD", "")
    resumes_list = cfg_data.get("resumes", [])

    if not email or not password:
        logger.error("Логин и пароль HH.ru не заданы ни в .env, ни в конфигурации!")
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
                success = hh.raise_resume(item)

                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                last_res_str = "УСПЕШНО ПОДНЯТО" if success else "Кнопка недоступна"
                update_resume_status(resume_id, now_str, last_res_str)

                logger.info(f"=== ЗАВЕРШЕНО: {resume_name} ===\n")

        except Exception as e:
            logger.critical(f"Скрипт завершился с ошибкой: {e}")
        finally:
            hh.close()

def main():
    parser = argparse.ArgumentParser(description="HH Resume Auto-Updater")
    parser.add_argument("--resume-id", type=str, help="ID резюме для обновления")
    args = parser.parse_args()

    run_update_for_resume(args.resume_id)

if __name__ == "__main__":
    main()
