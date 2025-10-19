import sys
import requests
import logging
import time
import telebot
import webbrowser

# Конфигурация
TOKEN = '6866886370:AAF-Xr196PsV3jJwgtljyHXxN1I0GWjPm_Y'
bot = telebot.TeleBot(TOKEN)
admins = [237736221]

client_id = 'KATBUHAPQ19FBB96GTM8MV98VKGAQUP4BRM2R3G0GVBB57RRA4TNBSHQ3EH0Q0CL'
client_secret = 'UQUEG1P6GRJJVLDL2U5PN5U58FHCLSVN1519TNARVPBRBMEVM9FPBRGAVKU457QP'
resume_id = '7c896da7ff07cdf4bc0039ed1f594776395242'
redirect_uri = 'https://example.com/callback'  # Замените на ваш redirect_uri

# Логирование
with open("/tmp/hh_cron_debug.log", "a") as log_file:
    log_file.write("hh.py started from cron\n")


# Отправка сообщений в Telegram
def send_message(chat_id, title, text):
    bot.send_message(chat_id, f"{title}:\n {text}")


# Обработка ошибок API
def handle_error(response_json, admin_id):
    description = response_json.get('description', 'Unknown error')
    errors = response_json.get('errors', [])
    error_messages = ', '.join([error.get('value', 'Unknown') for error in errors])
    error_text = f"description: {description}.\n Errors: {error_messages}"
    send_message(admin_id, 'Response', error_text)


# Получение кода авторизации
@bot.message_handler(commands=['authorize'])
def send_authorization_link(message):
    if message.chat.id not in admins:
        send_message(message.chat.id, 'Ошибка', 'У вас нет прав для выполнения этой команды.')
        return
    auth_url = f'https://hh.ru/oauth/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}'
    send_message(message.chat.id, 'Авторизация',
                 f'Перейдите по ссылке для авторизации:\n{auth_url}\nПосле авторизации отправьте полученный код командой /code <код>')
    # Открываем ссылку в браузере (опционально)
    webbrowser.open(auth_url)


# Обработка кода авторизации
@bot.message_handler(commands=['code'])
def handle_authorization_code(message):
    if message.chat.id not in admins:
        send_message(message.chat.id, 'Ошибка', 'У вас нет прав для выполнения этой команды.')
        return
    try:
        code = message.text.split()[1]  # Извлекаем код из сообщения
        get_tokens_from_code(code, message.chat.id)
    except IndexError:
        send_message(message.chat.id, 'Ошибка', 'Пожалуйста, укажите код авторизации: /code <код>')


# Обмен кода на токены
def get_tokens_from_code(code, chat_id):
    try:
        url = 'https://hh.ru/oauth/token'
        data = {
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
            'redirect_uri': redirect_uri
        }
        response = requests.post(url, data=data)
        response.raise_for_status()
        response_json = response.json()

        # Сохранение токенов
        with open('/root/python/autoupdate-hh-resume/access_token', 'w') as file:
            file.write(response_json.get('access_token'))
        with open('/root/python/autoupdate-hh-resume/refresh_token', 'w') as file:
            file.write(response_json.get('refresh_token'))

        send_message(chat_id, 'Успех', 'Токены успешно получены и сохранены!')
    except Exception as e:
        send_message(chat_id, 'Ошибка', f'Не удалось получить токены: {str(e)}')


# Обновление токенов
def get_refresh_access_token():
    try:
        with open('/root/python/autoupdate-hh-resume/refresh_token', 'r') as file:
            refresh_token = file.read()
        url = 'https://hh.ru/oauth/token'
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret
        }
        response = requests.post(url, data=data)
        response.raise_for_status()
        response_json = response.json()

        # Сохранение новых токенов
        with open('/root/python/autoupdate-hh-resume/refresh_token', 'w') as file:
            file.write(response_json.get('refresh_token'))
        with open('/root/python/autoupdate-hh-resume/access_token', 'w') as file:
            file.write(response_json.get('access_token'))
    except Exception as e:
        for admin in admins:
            send_message(admin, 'Ошибка обновления токена', f'Не удалось обновить токены: {str(e)}')


# Основной запрос для обновления резюме
def send_request():
    try:
        with open('/root/python/autoupdate-hh-resume/access_token', 'r') as file:
            access_token = file.read()
    except FileNotFoundError:
        for admin in admins:
            send_message(admin, 'Ошибка', 'Токен не найден. Пройдите авторизацию с помощью команды /authorize')
        return

    url = f'https://api.hh.ru/resumes/{resume_id}/publish'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'HH-User-Agent': 'autoupdate-hh-resume/1.0 (awjon94@gmail.com)'
    }

    attempt_count = 0
    max_attempts = 5

    while attempt_count < max_attempts:
        r = requests.post(url, headers=headers)
        if r.status_code == 403 and r.json().get('oauth_error') == 'token-expired':
            get_refresh_access_token()
            attempt_count += 1
            # Обновляем токен и повторяем запрос
            try:
                with open('/root/python/autoupdate-hh-resume/access_token', 'r') as file:
                    access_token = file.read()
                headers['Authorization'] = f'Bearer {access_token}'
            except FileNotFoundError:
                for admin in admins:
                    send_message(admin, 'Ошибка', 'Не удалось обновить токен.')
                return

        elif r.status_code == 204:
            for admin in admins:
                send_message(admin, 'Успех', f'Ваше резюме https://hh.ru/resume/{resume_id} успешно обновлено')
            return

        elif r.status_code in [429, 403, 404, 400]:
            for admin in admins:
                send_message(admin, 'Ошибка', f'Проблема с обновлением резюме. Код ошибки: {r.status_code}')
                handle_error(r.json(), admin)
            attempt_count += 1
            time.sleep(60)
        else:
            break

    for admin in admins:
        send_message(admin, 'Ошибка', f'Не удалось обновить резюме после {max_attempts} попыток')


# Запуск бота для обработки команд
if __name__ == '__main__':
    try:
        bot.polling(none_stop=True)
    except Exception as e:
        print(f"Ошибка бота: {e}")
        time.sleep(15)