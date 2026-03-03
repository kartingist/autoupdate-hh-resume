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
  'x-xsrftoken': 'cd3f649a1efbe2b4e418df420e31dbe2',
  'Cookie': '__ddg1_=G2eZgzfX6st2IgleMEyP; hhuid=qH4czzl47ce67WmVhPczFA--; iap.uid=fe4146a5ca3942968455934354f52bb7; tmr_lvid=6679cb07e9283bc972b3539b24a79ec8; tmr_lvidTS=1771368414506; _ym_uid=1771368415771679692; _ym_d=1771406585; region_clarified=NOT_SET; hhul=5c0a850138320daa45477dd96b62e47d02e6a61d77a2dc5f8ea9f19e63c68769; uxs_uid=42790290-052d-11f1-90eb-e9e57ec1269e; regions=53; cookie_policy_agreement=true; __ddg9_=193.233.121.18; display=desktop; crypted_hhuid=D95B5F76F6F373F1A82AAD728F57F678AA02022A3B20F6D73D8DBC31618C18F0; _xsrf=cd3f649a1efbe2b4e418df420e31dbe2; GMT=3; __zzatgib-w-hh=MDA0dC0jViV+FmELHw4/aQsbSl1pCENQGC9LXywwQWYfZnpfKEdcUjQpIkJ4a1hXORNhb0YneltAZSNfOVURCxIXRF5cVWl1FRpLSiVueCplJS0xViR8SylEXFR7Kh0VCHAqUQ4TVy8NPjteLW8PJwsSWAkhCklpC15zXV8WPkQhbAs4LBtFOA8teB1keEdXa1kZETUlKlU6FFxARW58Mm5pUl9KFSBIEk40Vk8WenFUVD08YkJDbTAbN1ddHBEkWA4hPwsXXFU+NVQOPHVXLw0uOF4tbx5mTlskR1lWfiwbGH5nFRtQSxgvS18zWn4lDzRDS1sKFA4/VFFCQisVWVJ1KW59OjAbRVcgaE9bIExeVGshC1E0NWYQSk9NRzM4P2h9HlQcOVURDxYSNhcjEn5yJVQLD2RCSG96MDdXYTAPFhFNRxU9VlJPQyhrG3FYMA==8JaUCw==; __zzatgib-w-hh=MDA0dC0jViV+FmELHw4/aQsbSl1pCENQGC9LXywwQWYfZnpfKEdcUjQpIkJ4a1hXORNhb0YneltAZSNfOVURCxIXRF5cVWl1FRpLSiVueCplJS0xViR8SylEXFR7Kh0VCHAqUQ4TVy8NPjteLW8PJwsSWAkhCklpC15zXV8WPkQhbAs4LBtFOA8teB1keEdXa1kZETUlKlU6FFxARW58Mm5pUl9KFSBIEk40Vk8WenFUVD08YkJDbTAbN1ddHBEkWA4hPwsXXFU+NVQOPHVXLw0uOF4tbx5mTlskR1lWfiwbGH5nFRtQSxgvS18zWn4lDzRDS1sKFA4/VFFCQisVWVJ1KW59OjAbRVcgaE9bIExeVGshC1E0NWYQSk9NRzM4P2h9HlQcOVURDxYSNhcjEn5yJVQLD2RCSG96MDdXYTAPFhFNRxU9VlJPQyhrG3FYMA==8JaUCw==; _ibc=False; hhtoken=bUgfMZTnS35UR4tsXe0yNfgekDZ_; cfidsgib-w-hh=cbPY2+6azZcpU2GPE3tSYv0iBeHReky66xEWigOrNJbVbmUveJWQkMpEvj3iWBVvTe6ms6uMqrVmUT9VdYwqLIYBzWW8ntM8SV+CcJRHw4dLR5zVxzco/kUcNLOxCuJZ7A/Ncsb3nkiJbu5GaOEJ/upmDYfmF8wv+HKvsSct; cfidsgib-w-hh=cbPY2+6azZcpU2GPE3tSYv0iBeHReky66xEWigOrNJbVbmUveJWQkMpEvj3iWBVvTe6ms6uMqrVmUT9VdYwqLIYBzWW8ntM8SV+CcJRHw4dLR5zVxzco/kUcNLOxCuJZ7A/Ncsb3nkiJbu5GaOEJ/upmDYfmF8wv+HKvsSct; gsscgib-w-hh=OWTkz4LBwJDZG4fMraM197VJgIsyiPldL1AYrJBTh7ya3ZSEVGGuVM5OvpMU4i0P2SWPEREGrcu2t1SJJTQ47aJWFJbXxRDLUrY1dOKIF3O9/bApIu8QA8dp6UfVB8DHfsrIW/yKnl3lR6mv7/PML/EtXE0eSTdD39OnvrXXQqwXNesjzGSmPwMbDM/xfdtT7ba1DZ8Xb/A7I7sxRY47w05KrFW2QhvtIuLwTtT00OROiUFhhEpddqLZfRJbrA==; gsscgib-w-hh=OWTkz4LBwJDZG4fMraM197VJgIsyiPldL1AYrJBTh7ya3ZSEVGGuVM5OvpMU4i0P2SWPEREGrcu2t1SJJTQ47aJWFJbXxRDLUrY1dOKIF3O9/bApIu8QA8dp6UfVB8DHfsrIW/yKnl3lR6mv7/PML/EtXE0eSTdD39OnvrXXQqwXNesjzGSmPwMbDM/xfdtT7ba1DZ8Xb/A7I7sxRY47w05KrFW2QhvtIuLwTtT00OROiUFhhEpddqLZfRJbrA==; device_magritte_breakpoint=s; device_breakpoint=s; fgsscgib-w-hh=ybrQaa9a7426bf1f943fdbbeca9ab78bd4b0d004; fgsscgib-w-hh=ybrQaa9a7426bf1f943fdbbeca9ab78bd4b0d004; just_logged_in=1; hhrole=applicant; _hi=39821997; gsscgib-w-hh=2foaLyWTtY4NnwTe6ueYLZ+u6G7ogsKR/B1FLL8L41jglQedlHTGG+JDXPA7vt8u821HyRRq+3YagNcJgypu5aqOU2MIRX09ghiB3lfGgLUIwGsM5L4EFQUBthWtKBI7mN+/IcxuogM7TzQzKAF4BKH9WimZ5xXrr+nss4L/JR+vcY99Ygk/57NO0IZMyIk+vhw8tDRvj3o468G7XRxGCW7VKQng7GhN94l1ryYKEDgM3Lr8uoMd7vKMHROBig==; cfidsgib-w-hh=3GxW7N0FfrJEtX+kG7zCkG6Lwr7riuyXpn54PPb6nTC12VcSqJqvdwK+GYPMAh8EsQq2nVgH1125WxNwM0qC0LeZYxYSXDE9d42nTcyiditFiP+wPbJqnz6QUeTih+PMJ0ZFNtu7ndkbOi6NOKVr9IclScz5mmaGEVaE7eST; crypted_id=D785A71578CD7A474EAD7FF9B79BC125A5471C6F722104504734B32F0D03E9F8; __ddg10_=1772545086; __ddg8_=EZEHDd7CwuoF158f'
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

