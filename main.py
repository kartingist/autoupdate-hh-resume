import os
import re
from playwright.sync_api import Playwright, sync_playwright

AUTH_FILE = "hh_session.json"


def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)

    context = None
    page = None

    try:
        if os.path.exists(AUTH_FILE):
            print("Найдена сохраненная сессия...")
            context = browser.new_context(storage_state=AUTH_FILE)
        else:
            print("Сессия не найдена...")
            context = browser.new_context()

        page = context.new_page()

        # 1. Переходим на страницу резюме
        page.goto("https://krasnodar.hh.ru/resume/7c896da7ff07cdf4bc0039ed1f594776395242",
                  wait_until="domcontentloaded")

        # 2. Проверяем, нужно ли логиниться
        login_button = page.get_by_role("button", name="Войти").first

        if login_button.is_visible():
            print("Кнопка 'Войти' видна. Логинимся...")
            login_button.click()
            login_button.click()

            page.locator("div").filter(has_text=re.compile(r"^Почта$")).first.click()
            page.get_by_role("textbox").fill("karting-35@ya.ru")
            page.get_by_role("button", name="Войти с паролем").click()
            page.get_by_role("textbox").fill("359325Aw")
            page.get_by_role("button", name="Войти", exact=True).click()

            print("Ждем завершения авторизации...")
            try:
                # Ожидаем конкретный span с текстом
                page.wait_for_selector("span:has-text('Ваша активность')", timeout=15000)
                print("Элемент найден, продолжаем...")
            except Exception as e:
                print("Не дождались селектора авторизации, сохраняем скриншот auth_error.png...")
                if page:
                    page.screenshot(path="auth_error.png", full_page=True)
                raise

            # Сохраняем куки
            context.storage_state(path=AUTH_FILE)
            print("Сессия сохранена.")


        else:
            print("Авторизация уже активна.")

        # Возвращаемся на страницу резюме
        page.goto("https://krasnodar.hh.ru/resume/7c896da7ff07cdf4bc0039ed1f594776395242", wait_until="domcontentloaded")

        # 3. Кликаем кнопку "Поднять"
        button = page.get_by_text("Поднять в поиске")
        print(button.text_content())
        try:
            button.wait_for(state="visible", timeout=5000)
            print("Кнопка найдена, нажимаю...")
            button.click()
        except Exception:
            print("Кнопка 'Поднять в поиске' не появилась, сохраняем скриншот button_error.png...")
            if page:
                page.screenshot(path="button_error.png", full_page=True)
            raise

        print("Готово")
        # page.wait_for_timeout(30000)

    except Exception as e:
        print(f"\n[КРИТИЧЕСКАЯ ОШИБКА] {e}")
        if page:
            page.screenshot(path="critical_error.png", full_page=True)
            print("Скриншот с ошибкой сохранен в файл 'critical_error.png'")
        raise

    finally:
        if context:
            context.close()
        if browser:
            browser.close()


with sync_playwright() as playwright:
    run(playwright)