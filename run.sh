#!/bin/sh
# Переходим в папку скрипта
cd /root/python/autoupdate-hh-resume
# Записываем дату
date >> hh_cron.log
# Запускаем питон и пишем вывод в тот же лог
/usr/bin/python -m hh >> hh_cron.log 2>&1