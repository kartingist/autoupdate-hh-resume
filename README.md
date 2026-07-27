# 💼 HH Resume Auto-Updater & Control Center

Автоматическая система автоподъема резюме на **HeadHunter (`hh.ru`)** с удобной веб-панелью управления, поддержкой неограниченного количества резюме, гибким расписанием по МСК и просмотром логов выполнения в реальном времени.

---

## 🌟 Основные возможности

- 🔄 **Автоматический подъем резюме:** Работает по гибкому расписанию (по умолчанию 5 раз в день: 07:00, 11:01, 15:02, 19:03, 23:04 MSK).
- 💼 **Мульти-резюме:** Добавление, настройка и удаление произвольного количества резюме в режиме онлайн.
- 🔐 **Веб-панель управления в стиле Dark Mode:**
  - Изменение учетных данных (Email / Пароль HH.ru) прямо с веб-страницы.
  - Независимые переключатели (Вкл / Выкл) для каждого резюме.
  - Редактирование названий, прямых ссылок HH URL и расписания.
  - Защита от потери введенных данных при смене фокуса ввода.
- 🚀 **Мгновенный ручной подъем:** Кнопка *«Поднять сейчас»* для вызова автоподъема вне расписания.
- 📜 **Живые логи без дублирования:** Отдельные терминальные окна с автоскроллом и историей до 150 строк на каждое резюме.
- 🔑 **Сохранение сессии:** Паузы и обход повторных входов через сохранение cookies (`hh_session.json`).

---

## 📁 Структура проекта

```
/home/heatcliff/autoupdate-hh-resume/
├── main.py                  # Основной Playwright-скрипт автоподъема
├── server.py                # Веб-сервер управления и фронтенд (порт 8883)
├── resumes_config.json      # Конфигурация аккаунта, списка резюме и расписаний
├── hh_session.json          # Сохраненный токен сессии авторизации HH.ru
├── resume1.log              # Лог выполнения для Резюме 1
├── resume2.log              # Лог выполнения для Резюме 2
└── venv/                    # Python Virtual Environment (Playwright)
```

---

## 🚀 Быстрый старт и установка

### 1. Требования
- **Linux OS** (Arch Linux, Ubuntu, Debian и т.д.)
- **Python 3.10+**
- **Chromium / Playwright**

### 2. Клонирование и установка зависимостей
```bash
git clone <repository_url> /home/heatcliff/autoupdate-hh-resume
cd /home/heatcliff/autoupdate-hh-resume

# Создание виртуального окружения
python3 -m venv venv
source venv/bin/activate

# Установка Playwright
pip install playwright
playwright install chromium
```

### 3. Автоматический запуск панели как Systemd Service
Для того чтобы веб-панель работала в фоновом режиме 24/7, создайте сервис systemd:

```bash
sudo cat << 'SERVICE' > /etc/systemd/system/hh-dashboard.service
[Unit]
Description=HH Multi-Resume Control Center Web Dashboard
After=network.target

[Service]
Type=simple
User=heatcliff
WorkingDirectory=/home/heatcliff/autoupdate-hh-resume
ExecStart=/usr/bin/python3 /opt/hh_dashboard/server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

# Включение и запуск службы
sudo systemctl daemon-reload
sudo systemctl enable --now hh-dashboard.service
```

---

## 💻 Использование Веб-Панели

Откройте в браузере: **`http://<IP_СЕРВЕРА>:8883`** (или через проброшенный порт/туннель).

### 1. Настройка учетных данных HH.ru
1. В верхнем блоке **«🔐 Авторизация аккаунта HH.ru»** введите логин (Email) и пароль от вашего аккаунта HH.
2. Нажмите **«💾 Сохранить данные входа»**.

### 2. Добавление и настройка резюме
1. Нажмите кнопку **«➕ Добавить резюме»** в шапке.
2. В появившейся карточке укажите:
   - **Название:** Произвольное понятное имя (например, `Senior Python Developer`).
   - **Ссылка на резюме (HH URL):** Полная ссылка вида `https://krasnodar.hh.ru/resume/7c896da7ff07cdf4bc...`.
   - **Время автоподъема (ЧЧ:ММ MSK):** Время через запятую (например, `07:00, 11:01, 15:02, 19:03, 23:04`).
3. Нажмите **«💾 Сохранить настройки»**. Системное расписание `crontab` обновится автоматически!

### 3. Ручной запуск
Нажмите **«🚀 Поднять сейчас»** на нужной карточке, чтобы проверить работу Playwright и запустить автоподъем прямо сейчас.

---

## 🔧 Запуск из командной строки (CLI)

Для тестирования конкретного резюме из терминала:

```bash
# Поднять Резюме 1
/home/heatcliff/autoupdate-hh-resume/venv/bin/python /home/heatcliff/autoupdate-hh-resume/main.py --resume-id resume1

# Поднять Резюме 2
/home/heatcliff/autoupdate-hh-resume/venv/bin/python /home/heatcliff/autoupdate-hh-resume/main.py --resume-id resume2
```

---

## 🛠 Обслуживание и устранение неполадок

- **Кнопка «Поднять в поиске» не доступна:**  
  HeadHunter разрешает поднимать резюме 1 раз в 4 часа. Если время еще не прошло, скрипт зафиксирует статус *«Кнопка недоступна»* и сделает скриншот в папку проекта.
- **Сброс авторизации:**  
  Если сессия истекла, удалите файл `hh_session.json`, и скрипт выполнит повторный логин по введенным паролям.

---
