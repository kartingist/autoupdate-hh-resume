import requests

# 1. Создаем объект сессии
session = requests.Session()



payload = {'username': 'karting-35@ya.ru',
'password': '359325Aw',
'accountType': 'APPLICANT',
'failUrl': '/account/login?backurl=%2F&role=applicant',
'remember': 'true',
'captchaText': ''}

headers = {
  'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36',
  'x-xsrftoken': 'aae5b8ea7ae4d75131b0d404f62b989c',
  'Cookie': '__ddg10_=1770582496; __ddg1_=wagUYJPhHSaeqBNg4J9m; __ddg8_=XEA8Y62NOdUQUh2x; __ddg9_=193.233.122.56; _xsrf=aae5b8ea7ae4d75131b0d404f62b989c; crypted_hhuid=44ED10FB17142425C8B86C13C4234CFFB559CD7F5AD675452A02DA60CA4FC3D6; crypted_id=D785A71578CD7A474EAD7FF9B79BC125A5471C6F722104504734B32F0D03E9F8; hhrole=anonymous; hhtoken=IQGhskrtIZesvLF13LOC3r5XqLBO; hhuid=xz!wEctf7sJd_2lYDMg7DQ--; hhul=bd307c89b40b6bb3a6486d7306b3ec1ae0df8bf88e2f4f28125dd0db656c2e2b; __ddg10_=1770582467; __ddg8_=gKsS3fELq6Txu0xa; __ddg9_=193.233.122.56; _hi=39821997; display=desktop; just_logged_in=1; regions=53'
}

# 2. Первый запрос (авторизация)
# Сессия автоматически получит и сохранит куки
auth_url = "https://krasnodar.hh.ru/account/login?backurl=%2F&role=applicant"
response1 = session.post(auth_url, data=payload, headers=headers)

# Проверяем, что авторизация прошла успешно
if response1.status_code == 200:
    print("Авторизация успешна!")

    # 3. Второй запрос (сама команда)
    # Куки из response1 подставятся автоматически
    command_url = 'https://krasnodar.hh.ru/applicant/resumes/touch'
    payload = {'resume': '7c896da7ff07cdf4bc0039ed1f594776395242',
               'undirectable': 'true'}
    response2 = session.post(command_url, data=payload, headers=headers)
    print(response2.status_code)

