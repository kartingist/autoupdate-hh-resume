import requests
import webbrowser

# Конфигурация
client_id = 'KATBUHAPQ19FBB96GTM8MV98VKGAQUP4BRM2R3G0GVBB57RRA4TNBSHQ3EH0Q0CL'
client_secret = 'UQUEG1P6GRJJVLDL2U5PN5U58FHCLSVN1519TNARVPBRBMEVM9FPBRGAVKU457QP'


# Ссылка для авторизации (перейдите по ней в браузере, авторизуйтесь и скопируйте код из URL):
# https://hh.ru/oauth/authorize?response_type=code&client_id=KATBUHAPQ19FBB96GTM8MV98VKGAQUP4BRM2R3G0GVBB57RRA4TNBSHQ3EH0Q0CL&redirect_url=https://example.com/callback

# Функция для обмена кода на токены
def get_tokens_from_code(code):
    try:
        url = 'https://hh.ru/oauth/token'
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = {
            'grant_type': 'authorization_code',
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code
        }
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        response_json = response.json()

        # Сохранение токенов
        with open('access_token', 'w') as file:
            file.write(response_json.get('access_token', ''))
        with open('refresh_token', 'w') as file:
            file.write(response_json.get('refresh_token', ''))

        print('Токены успешно получены и сохранены в файлы access_token и refresh_token!')
    except requests.exceptions.HTTPError as http_err:
        print(f'HTTP ошибка при получении токенов: {http_err}')
        if response.status_code == 400:
            print('Проверьте правильность кода авторизации или redirect_uri.')
    except Exception as e:
        print(f'Ошибка при получении токенов: {str(e)}')


if __name__ == '__main__':
    # Формируем ссылку для авторизации
    auth_url = f'https://hh.ru/oauth/authorize?response_type=code&client_id={client_id}'
    print(f'1. Перейдите по ссылке для авторизации:\n{auth_url}')
    print('2. Авторизуйтесь на hh.ru и скопируйте код из URL (например, code=ВАШ_КОД).')
    webbrowser.open(auth_url)

    # Запрашиваем код у пользователя
    code = input('Введите код авторизации из URL: ').strip()
    if code:
        get_tokens_from_code(code)
    else:
        print('Ошибка: код авторизации не введен.')