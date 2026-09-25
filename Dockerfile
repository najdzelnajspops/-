# См. docs/ARCHITECTURE.md §"Фазы" (MVP-критерий: "CLI запуск, Docker-упаковка")
# и docker/oda/README.md для опционального шага с ODA File Converter.

FROM python:3.14-slim-bookworm

# GEOS НЕ ставится отдельным системным пакетом ("apt install libgeos-dev"):
# shapely==2.1.2 приносит GEOS 3.13.1 СТАТИЧЕСКИ внутри manylinux wheel (см.
# requirements.txt, комментарий про повторяемость — docs/ARCHITECTURE.md §3.1).
# Системный libgeos создал бы риск версионного расхождения между dev-машиной
# и контейнером; полагаться на wheel — то же самое, что уже проверено
# кросс-платформенно (Windows dev vs WSL Ubuntu, оба через pip), см.
# docs/SESSION_LOG.md.
#
# xvfb + минимальный набор Qt6/X11-библиотек — ODA File Converter (опционально,
# см. docker/oda/README.md) это Qt6 GUI-приложение, ему нужен виртуальный
# дисплей даже в пакетном режиме конвертации.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        libgl1 \
        libegl1 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libxtst6 \
        libnss3 \
        libfontconfig1 \
        libxi6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ODA File Converter — см. docker/oda/README.md. Wildcard-COPY гарантированно
# не падает на "no such file or directory": в docker/oda/ всегда лежит хотя бы
# ODAFileConverter.PLACEHOLDER.txt (см. этот файл), даже пока настоящего
# AppImage там ещё нет. Если пользователь положил рядом реальный
# ODAFileConverter*.AppImage — распаковываем его СРАЗУ на этапе сборки
# (--appimage-extract), чтобы в рантайме не требовался /dev/fuse (в контейнерах
# обычно недоступен) — получаем docker/oda/squashfs-root/AppRun.
COPY docker/oda/ODAFileConverter* /opt/oda/
RUN cd /opt/oda && \
    for f in ODAFileConverter*.AppImage; do \
        if [ -f "$f" ]; then \
            chmod +x "$f" && ./"$f" --appimage-extract && rm -f "$f"; \
        fi; \
    done

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && sed -i 's/\r$//' /entrypoint.sh

COPY pipeline/ pipeline/
COPY tests/ tests/
COPY data/reference/ data/reference/
# data/demo_objects/ — заранее сконвертированные DXF для веб-UI (pipeline/web/),
# см. докстринг pipeline/web/demo_objects.py — коммитятся в git (небольшие,
# не 23-ГБ сырой датасет), нужны для живой демонстрации без ODA File Converter.
COPY data/demo_objects/ data/demo_objects/

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
# По умолчанию — CLI-справка (см. docs/TASK_BRIEF.md, CLI основной способ).
# Веб-UI — тем же образом, вторым способом запуска:
#   docker compose run --rm -p 8000:8000 app uvicorn pipeline.web.app:app --host 0.0.0.0 --port 8000
CMD ["python", "-m", "pipeline.run", "--help"]
