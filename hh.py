import sys
import requests
import logging
import time
import telebot

TOKEN = '6866886370:AAF-Xr196PsV3jJwgtljyHXxN1I0GWjPm_Y'
bot = telebot.TeleBot(TOKEN)
admins = [237736221]


def handle_error(response_json, admin_id):
    description = response_json.get('description', 'Unknown error')
    errors = response_json.get('errors', [])
    error_messages = ', '.join([error.get('value', 'Unknown') for error in errors])
    error_text = f"description: {description}.\n" \
                 f" Errors: {error_messages}"
    send_message(admin_id, 'Response', error_text)


def send_message(chat_id, title, text):
    bot.send_message(chat_id, f"{title}:\n {text}")


def send_request():
    global r
    if len(sys.argv) != 3:
        print('Usage: {} <file with token> <resume id>'.format(sys.argv[0]), file=sys.stderr)
        exit(1)

    resume_id = sys.argv[2]
    access_token = sys.argv[1]
    LOG_FILENAME = 'hh.log'
    logging.basicConfig(filename=LOG_FILENAME, level=logging.DEBUG,
                        format=u'%(levelname)-8s [%(asctime)s]  %(message)s')

    url = 'https://api.hh.ru/resumes/' + resume_id + '/publish'
    headers = {'Authorization': 'Bearer ' + access_token,
               'HH-User-Agent': 'autoupdate-hh-resume/1.0 (awjon94@gmail.com)'}

    attempt_count = 0
    max_attempts = 7

    while attempt_count < max_attempts:
        r = requests.post(url, headers=headers)
        logging.debug('Got response from server: %s', repr(r.text))
        if r.status_code == 403 and r.json().get('oauth_error') == 'token-expired':
            attempt_count += 1
            print('hello')
        elif r.status_code == 204:
            for admin in admins:
                send_message(admin, 'Success', 'Your resume https://hh.ru/resume/' + resume_id + ' is updated')
            return

        elif r.status_code in [429, 403, 404, 400]:
            for admin in admins:
                send_message(admin, 'Error', f'Problem with updating resume. Status code: {r.status_code}')
                handle_error(r.json(), admin)
                print(r.json())
            attempt_count += 1
            time.sleep(60)
        else:
            break

    for admin in admins:
        send_message(admin, 'Error', f'Failed to update resume after {max_attempts} attempts')


if __name__ == '__main__':
    send_request()
