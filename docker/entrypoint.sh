#!/bin/sh
# Точка входа контейнера. Два дела:
# 1. Поднимает виртуальный X-дисплей (Xvfb) — ODA File Converter (опционально,
#    см. docker/oda/README.md) это Qt6 GUI-приложение, ему нужен дисплей даже
#    в пакетном (headless) режиме конвертации. Запускается НАПРЯМУЮ, не через
#    `xvfb-run`: тот ждёт сигнал SIGUSR1 от Xvfb как признак готовности, а в
#    Docker (PID 1 в изолированном namespace) этот сигнал по факту не всегда
#    доходит до shell — `wait` внутри xvfb-run зависает навсегда (реально
#    воспроизведено при первой проверке образа, см. docs/SESSION_LOG.md,
#    2026-09-18). Вместо сигнала — опрос unix-сокета X-сервера, обычный
#    надёжный паттерн для Xvfb в контейнерах.
# 2. Автоопределение ODA File Converter (см. docker/oda/README.md) —
#    распакованный AppImage (docker/oda/ODAFileConverter -> squashfs-root/AppRun
#    в Dockerfile). pipeline/ingest/dwg_convert.py подхватит ODA_FILE_CONVERTER_PATH
#    сам (find_oda_converter() читает эту переменную первым делом).
set -e

Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99

i=0
while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 50 ]; do
    sleep 0.1
    i=$((i + 1))
done

if [ -z "$ODA_FILE_CONVERTER_PATH" ] && [ -x /opt/oda/squashfs-root/AppRun ]; then
    export ODA_FILE_CONVERTER_PATH=/opt/oda/squashfs-root/AppRun
fi

exec "$@"
