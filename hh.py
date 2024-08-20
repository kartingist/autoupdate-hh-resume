import sys
import requests
import logging
import time
import telebot

TOKEN = '6866886370:AAF-Xr196PsV3jJwgtljyHXxN1I0GWjPm_Y'
bot = telebot.TeleBot(TOKEN)
admins = [237736221]

# refresh_token = 'USERU1PNSCDC6KAP4QQ795S7NG2E91004TIEOU63TGFVOU57AA13HHFKC0Q0U52T'

client_id = 'KATBUHAPQ19FBB96GTM8MV98VKGAQUP4BRM2R3G0GVBB57RRA4TNBSHQ3EH0Q0CL'
client_secret = 'UQUEG1P6GRJJVLDL2U5PN5U58FHCLSVN1519TNARVPBRBMEVM9FPBRGAVKU457QP'
resume_id = '7c896da7ff07cdf4bc0039ed1f594776395242'


def send_message(chat_id, title, text):
    bot.send_message(chat_id, f"{title}:\n {text}")


def handle_error(response_json, admin_id):
    description = response_json.get('description', 'Unknown error')
    errors = response_json.get('errors', [])
    error_messages = ', '.join([error.get('value', 'Unknown') for error in errors])
    error_text = f"description: {description}.\n" \
                 f" Errors: {error_messages}"
    send_message(admin_id, 'Response', error_text)


def get_refresh_access_token():
    try:
        with open('/root/autoupdate-hh-resume/refresh_token', 'r') as file:
            refresh_token = file.read()
        headers = {
            'grant_type': '',
            'Content-Type': 'application/x-www-form-urlencoded',
        }

        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
        }

        response = requests.post('https://hh.ru/oauth/token', headers=headers, data=data)
        with open('/root/autoupdate-hh-resume/refresh_token', 'w') as file:
            file.write(response.json().get('refresh_token'))

        with open('/root/autoupdate-hh-resume/access_token', 'w') as file:
            file.write(response.json().get('access_token'))

    except:
        for admin in admins:
            send_message(chat_id=admin, title="Ошибка обновления токена", text='...')


def send_request():
    with open('/root/autoupdate-hh-resume/access_token', 'r') as file:
        access_token = file.read()
    url = f'https://api.hh.ru/resumes/{resume_id}/publish'
    headers = {'Authorization': f'Bearer {access_token}',
               'HH-User-Agent': 'autoupdate-hh-resume/1.0 (awjon94@gmail.com)'}

    attempt_count = 0
    max_attempts = 5

    while attempt_count < max_attempts:
        r = requests.post(url, headers=headers)
        if r.status_code == 403 and r.json().get('oauth_error') == 'token-expired':
            get_refresh_access_token()
            attempt_count += 1

        elif r.status_code == 204:
            for admin in admins:
                send_message(admin, 'Success', 'Your resume https://hh.ru/resume/' + resume_id + ' is updated')
            return

        elif r.status_code in [429, 403, 404, 400]:
            for admin in admins:
                send_message(admin, 'Error', f'Problem with updating resume. Status code: {r.status_code}')
                handle_error(r.json(), admin)
            attempt_count += 1
            time.sleep(60)
        else:
            break

    for admin in admins:
        send_message(admin, 'Error', f'Failed to update resume after {max_attempts} attempts')


if __name__ == '__main__':
    send_request()
