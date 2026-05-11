import os
import re
import logging
from datetime import datetime
from playwright.sync_api import Playwright, sync_playwright, Page, BrowserContext, Browser

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# --- Конфигурация ---
AUTH_FILE = "hh_session.json"
RESUME_URL = "https://krasnodar.hh.ru/resume/7c896da7ff07cdf4bc0039ed1f594776395242"
EMAIL = "karting-35@ya.ru"
PASSWORD = "359325Aw"


class HHAutomation:
    def __init__(self, playwright: Playwright):
        self.playwright = playwright
        self.browser: Browser = None
        self.context: BrowserContext = None
        self.page: Page = None

    def launch_browser(self, headless: bool = True):
        logger.info("Запуск браузера...")
        self.browser = self.playwright.chromium.launch(headless=headless)

        # Если запускаем через cron, лучше использовать полные пути к файлу сессии,
        # но так как в cron у вас прописан `cd`, относительный путь AUTH_FILE сработает.
        if os.path.exists(AUTH_FILE):
            logger.info(f"Загрузка сохраненной сессии из {AUTH_FILE}...")
            self.context = self.browser.new_context(storage_state=AUTH_FILE)
        else:
            logger.warning("Файл сессии не найден, будет создана новая сессия.")
            self.context = self.browser.new_context()

        self.page = self.context.new_page()

    def _save_screenshot(self, name: str):
        if self.page:
            filename = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            self.page.screenshot(path=filename, full_page=True)
            logger.info(f"Скриншот ошибки сохранен: {filename}")

    def is_logged_in(self) -> bool:
        logger.info("Проверка статуса авторизации...")
        try:
            self.page.goto(RESUME_URL, wait_until="domcontentloaded")
            login_button = self.page.get_by_role("button", name="Войти").first

            # Проверяем видимость кнопки "Войти" с коротким таймаутом
            if login_button.is_visible(timeout=5000):
                logger.info("Кнопка 'Войти' обнаружена. Нужно авторизоваться.")
                return False

            logger.info("Сессия валидна, мы внутри.")
            return True
        except Exception as e:
            logger.error(f"Ошибка при проверке авторизации: {e}")
            return False

    def perform_login(self):
        logger.info("Начало процесса входа...")
        try:
            login_btn = self.page.get_by_role("button", name="Войти").first
            login_btn.click()

            self.page.locator("div").filter(has_text=re.compile(r"^Почта$")).first.click()
            self.page.get_by_role("textbox").fill(EMAIL)

            self.page.get_by_role("button", name="Войти с паролем").click()
            self.page.get_by_role("textbox").fill(PASSWORD)
            self.page.get_by_role("button", name="Войти", exact=True).click()

            # Ждем появления признака успешного входа
            self.page.wait_for_selector("span:has-text('Ваша активность')", timeout=15000)

            # Сохраняем состояние сессии
            self.context.storage_state(path=AUTH_FILE)
            logger.info("Авторизация прошла успешно. Сессия обновлена и сохранена.")

        except Exception as e:
            logger.error(f"Критическая ошибка при попытке логина: {e}")
            self._save_screenshot("login_failed")
            raise

    def raise_resume(self):
        logger.info("Переход на страницу резюме для поднятия...")
        self.page.goto(RESUME_URL, wait_until="domcontentloaded")
        # 1. Закрываем баннер с куками, если он есть
        try:
            cookie_close = self.page.get_by_role("button", name="Понятно")
            if cookie_close.is_visible(timeout=3000):
                cookie_close.click()
                logger.info("Баннер кук закрыт.")
        except:
            pass
        button = self.page.get_by_text("Поднять в поиске")
        try:
            # Ждем кнопку, чтобы убедиться, что страница прогрузилась
            button.wait_for(state="visible", timeout=7000)
            logger.info("Кнопка 'Поднять в поиске' доступна. Нажимаю...")
            button.click()
            logger.info("РЕЗЮМЕ УСПЕШНО ПОДНЯТО!")
        except Exception:
            logger.warning("Кнопка 'Поднять в поиске' не найдена. Возможно, время еще не пришло.")
            self._save_screenshot("button_not_available")

    def close(self):
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        logger.info("Браузер закрыт, ресурсы освобождены.")


def main():
    logger.info("=== ЗАПУСК СКРИПТА ПО РАСПИСАНИЮ (CRON) ===")

    with sync_playwright() as playwright:
        hh = HHAutomation(playwright)
        try:
            hh.launch_browser(headless=True)

            if not hh.is_logged_in():
                hh.perform_login()

            hh.raise_resume()

        except Exception as e:
            logger.critical(f"Скрипт завершился с ошибкой: {e}")
        finally:
            hh.close()
            logger.info("=== РАБОТА ЗАВЕРШЕНА ===\n")


if __name__ == "__main__":
    main()