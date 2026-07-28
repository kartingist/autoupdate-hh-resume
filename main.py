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
def setup_logger(target_id=None):
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if target_id:
        log_file = os.path.join(BASE_DIR, f"{target_id}.log")
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
                        os.environ[k.strip()] = v.strip().strip('\"\'')
        except Exception as e:
            print('Error loading .env file:', e)
BASE_DIR = os.environ.get("HH_BASE_DIR") or os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE_DIR, "hh_session.json")
CONFIG_FILE = os.path.join(BASE_DIR, "resumes_config.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")

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
        try:
            cfg_data["resumes"] = resumes
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f, ensure_ascii=False, indent=2)
            pass
            logger.info(f"Статус {resume_id} успешно сохранен: {last_result}")
        except Exception as e:
            logger.error(f"Ошибка сохранения статуса {resume_id}: {e}")

class HHAutomation:
    def __init__(self, playwright: Playwright, email: str, password: str):
        self.playwright = playwright
        self.email = email
        self.password = password
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None

    def launch_browser(self, headless: bool = True):
        logger.info("Запуск браузера Chromium с режимом Stealth...")
        self.browser = self.playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )

        user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

        if os.path.exists(AUTH_FILE):
            logger.info(f"Загрузка сохраненной сессии из {AUTH_FILE}...")
            self.context = self.browser.new_context(
                storage_state=AUTH_FILE,
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
        self.page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def _save_screenshot(self, name: str):
        if self.page:
            filename = os.path.join(BASE_DIR, f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
            self.page.screenshot(path=filename, full_page=True)
            logger.info(f"Скриншот ошибки сохранен: {filename}")

    def is_logged_in(self, target_url: str) -> bool:
        logger.info("Проверка статуса авторизации...")
        try:
            self.page.goto(target_url, wait_until="domcontentloaded")
            login_button = self.page.get_by_role("button", name="Войти").first

            if login_button.is_visible(timeout=5000):
                logger.info("Кнопка 'Войти' обнаружена. Нужно авторизоваться.")
                return False

            logger.info("Сессия валидна, мы внутри.")
            return True
        except Exception as e:
            logger.error(f"Ошибка при проверке авторизации: {e}")
            return False

    def perform_login(self):
        logger.info(f"Начало процесса входа для {self.email}...")
        try:
            if "account/login" not in self.page.url:
                self.page.goto("https://hh.ru/account/login?role=applicant", wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)

            # Ensure Applicant role card is selected
            try:
                applicant_radio = self.page.locator("[data-qa*='APPLICANT']").first
                if applicant_radio.is_visible(timeout=2000):
                    applicant_radio.click(force=True)
                    logger.info("Выбран тип аккаунта 'Соискатель'.")
            except Exception as e:
                logger.warning(f"Выбор типа аккаунта: {e}")

            # Click initial 'Войти' button on landing card if present
            try:
                login_start = self.page.get_by_role("button", name=re.compile(r"^Войти$", re.I)).first
                if login_start.is_visible(timeout=2000):
                    login_start.click(force=True)
                    logger.info("Нажата стартовая кнопка 'Войти'.")
            except Exception as e:
                logger.warning(f"Стартовая кнопка 'Войти': {e}")

            self.page.wait_for_timeout(2000)

            # Click 'Почта' tab if present
            try:
                mail_tab = self.page.get_by_text(re.compile(r"^Почта$", re.I)).first
                if mail_tab.is_visible(timeout=2000):
                    mail_tab.click(force=True)
                    logger.info("Вкладка 'Почта' нажата (force=True).")
            except Exception as e:
                logger.warning(f"Клик по вкладке 'Почта': {e}")

            # Fill email
            email_input = self.page.get_by_placeholder(re.compile(r"Электронная почта|Email|почта", re.I)).first
            if not email_input.is_visible(timeout=2000):
                email_input = self.page.locator("input[name='login'], input[type='text']").first
            email_input.fill(self.email)
            logger.info("Email успешно введен.")

            # Click 'Войти с паролем'
            try:
                pass_btn = self.page.get_by_role("button", name=re.compile(r"Войти с паролем", re.I)).first
                if pass_btn.is_visible(timeout=2000):
                    pass_btn.click(force=True)
                    logger.info("Кнопка 'Войти с паролем' нажата.")
            except Exception as e:
                logger.warning(f"Переход к вводу пароля: {e}")

            # Fill password
            pass_input = self.page.get_by_placeholder(re.compile(r"Пароль", re.I)).first
            if not pass_input.is_visible(timeout=2000):
                pass_input = self.page.locator("input[type='password']").first
            pass_input.fill(self.password)
            logger.info("Пароль успешно введен.")

            # Click final 'Войти' button
            login_submit = self.page.get_by_role("button", name=re.compile(r"^Войти$", re.I)).first
            login_submit.click(force=True)
            logger.info("Нажата финишная кнопка 'Войти'.")

            self.page.wait_for_timeout(4000)
            self.context.storage_state(path=AUTH_FILE)
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

        logger.info(f"Переход на страницу {resume_name}: {resume_url}")
        self.page.goto(resume_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        
        # Закрываем баннеры кук если есть
        for cookie_text in ["Понятно", "Принять", "Закрыть"]:
            try:
                cookie_close = self.page.get_by_role("button", name=cookie_text).first
                if cookie_close.is_visible(timeout=1500):
                    cookie_close.click(force=True)
                    logger.info(f"Баннер '{cookie_text}' закрыт.")
            except Exception:
                pass

        # Ищем кнопку поднятия резюме по мульти-селекторам
        button_selectors = [
            "[data-qa*='resume-update']",
            "button:has-text('Поднять в поиске')",
            "a:has-text('Поднять в поиске')",
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
                logger.info(f"Кнопка 'Поднять в поиске' доступна для {resume_name}. Нажимаю (force=True)...")
                button.click(force=True)
                self.page.wait_for_timeout(2000)
                logger.info(f"РЕЗЮМЕ УСПЕШНО ПОДНЯТО: {resume_name}!")
                return True
            except Exception as e:
                logger.error(f"Ошибка при клике на кнопку подъема: {e}")
                self._save_screenshot("button_click_failed")
                return False
        else:
            logger.warning(f"Кнопка 'Поднять в поиске' не найдена для {resume_name}. Возможно, время еще не пришло.")
            self._save_screenshot("button_not_available")
            return False

        logger.info(f"Переход на страницу {resume_name}: {resume_url}")
        self.page.goto(resume_url, wait_until="domcontentloaded")
        
        try:
            cookie_close = self.page.get_by_role("button", name="Понятно")
            if cookie_close.is_visible(timeout=3000):
                cookie_close.click()
                logger.info("Баннер кук закрыт.")
        except Exception:
            pass

        button = self.page.get_by_text("Поднять в поиске")
        try:
            button.wait_for(state="visible", timeout=7000)
            logger.info(f"Кнопка 'Поднять в поиске' доступна для {resume_name}. Нажимаю...")
            button.click()
            logger.info(f"РЕЗЮМЕ УСПЕШНО ПОДНЯТО: {resume_name}!")
            return True
        except Exception:
            logger.warning(f"Кнопка 'Поднять в поиске' не найдена для {resume_name}. Возможно, время еще не пришло.")
            self._save_screenshot("button_not_available")
            return False

    def close(self):
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        logger.info("Браузер закрыт, ресурсы освобождены.")


def run_update_for_resume(target_id=None):
    setup_logger(target_id)
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
        hh = HHAutomation(playwright, email=email, password=password)
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
