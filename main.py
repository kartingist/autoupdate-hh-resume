import os
import sys
import re
import json
import logging
import argparse
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright, Page, BrowserContext, Browser

# --- Настройка логирования (ТОЛЬКО stdout, без продублированных FileHandler) ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE_DIR, "hh_session.json")
CONFIG_FILE = os.path.join(BASE_DIR, "resumes_config.json")

def load_resumes_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {"auth": {"email": "karting-35@ya.ru", "password": "359325Aw"}, "resumes": data}
                return data
        except Exception as e:
            logger.error(f"Ошибка чтения конфига резюме: {e}")
    return {"auth": {"email": "karting-35@ya.ru", "password": "359325Aw"}, "resumes": []}

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
            os.system(f"chown heatcliff:heatcliff {CONFIG_FILE}")
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
        logger.info("Запуск браузера Chromium...")
        self.browser = self.playwright.chromium.launch(headless=headless)

        if os.path.exists(AUTH_FILE):
            logger.info(f"Загрузка сохраненной сессии из {AUTH_FILE}...")
            self.context = self.browser.new_context(storage_state=AUTH_FILE)
        else:
            logger.warning("Файл сессии не найден, будет создана новая сессия.")
            self.context = self.browser.new_context()

        self.page = self.context.new_page()

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
            login_btn = self.page.get_by_role("button", name="Войти").first
            login_btn.click()
            login_btn = self.page.get_by_role("button", name="Войти").first
            login_btn.click()

            self.page.locator("div").filter(has_text=re.compile(r"^Почта$")).first.click()
            self.page.get_by_role("textbox").fill(self.email)

            self.page.get_by_role("button", name="Войти с паролем").click()
            self.page.get_by_role("textbox").fill(self.password)
            self.page.get_by_role("button", name="Войти", exact=True).click()

            logger.info("Ждем появления вкладки 'Резюме и профиль'...")
            self.page.get_by_text(re.compile(r"Резюме\s+и\s+профиль")).wait_for(state="visible", timeout=15000)

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
    cfg_data = load_resumes_config()
    auth_info = cfg_data.get("auth", {})
    email = auth_info.get("email") or "karting-35@ya.ru"
    password = auth_info.get("password") or "359325Aw"
    resumes_list = cfg_data.get("resumes", [])

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
