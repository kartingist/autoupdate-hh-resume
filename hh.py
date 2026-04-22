import requests

session = requests.Session()

# 1. Сначала просто заходим на сайт, чтобы получить свежие куки и XSRF
base_url = "https://krasnodar.hh.ru/account/login"
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Accept': '*/*'
}

initial_response = session.get(base_url, headers=headers)
# Извлекаем токен из кук сессии
xsrf_token = session.cookies.get('_xsrf')

# 2. Логин
payload = {
    'username': 'karting-35@ya.ru',
    'password': '359325Aw',
    'backurl': '/',
    'role': 'applicant',
    'remember': 'yes',
    '_xsrf': xsrf_token  # Обязательно добавляем в payload
}

# Обновляем заголовки для авторизации
auth_headers = headers.copy()
auth_headers['X-XSRF-TOKEN'] = xsrf_token
auth_headers['Referer'] = base_url

auth_response = session.post(base_url, data=payload, headers=auth_headers)

if auth_response.status_code == 200:
    print("Авторизация успешна!")

    # 1. Свежий токен ИЗ КУК после логина
    # Это критически важно, он часто меняется после auth
    current_xsrf = session.cookies.get('_xsrf')

    touch_url = 'https://krasnodar.hh.ru/applicant/resumes/touch'

    # 2. Формируем заголовки максимально похожими на браузерные
    touch_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest',  # Сообщаем, что это AJAX запрос
        'X-XSRF-TOKEN': current_xsrf,
        'Referer': 'https://krasnodar.hh.ru/applicant/resumes',  # С какой страницы нажали
        'Origin': 'https://krasnodar.hh.ru'
    }

    payload = {
        'resume': '7c896da7ff07cdf4bc0039ed1f594776395242',
        'undirectable': 'true'
    }

    # 3. Делаем запрос
    response2 = session.post(touch_url, data=payload, headers=touch_headers)

    print(f"Статус обновления: {response2.status_code}")
    if response2.status_code == 403:
        print("Сервер отклонил запрос. Проверьте куку _xsrf в консоли браузера.")