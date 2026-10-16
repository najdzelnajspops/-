#!/bin/sh
# Запуск веб-UI на Linux (MosTex.OS, Ubuntu) без Docker. Первый запуск создаёт
# локальное окружение .venv и ставит в него requirements.txt; дальше — сразу старт.
# Остановить сервис: Ctrl+C.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    PY=$(command -v python3.14 || command -v python3 || true)
    if [ -z "$PY" ]; then
        echo "Не найден Python 3. Нужен Python 3.14 (проверено на нём)."
        exit 1
    fi
    echo "Первый запуск: создаю окружение .venv и ставлю зависимости (один раз, несколько минут)..."
    if ! "$PY" -m venv .venv; then
        echo "Не удалось создать окружение. На Ubuntu/Debian: sudo apt install python3-venv"
        rm -rf .venv
        exit 1
    fi
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/python -m pip install -r requirements.txt
fi

echo "Сервис: http://127.0.0.1:8000  (Swagger: http://127.0.0.1:8000/docs). Остановить: Ctrl+C"
exec .venv/bin/python -m uvicorn pipeline.web.app:app --host 127.0.0.1 --port 8000
