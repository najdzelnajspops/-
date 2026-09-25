# Используемые методы и библиотеки

Обязательный документ по ТЗ (`docs/TASK_BRIEF.md`, §6.4).

## Подход к «мозгу» алгоритма

Выбраны **детерминированные правила и геометрические буферы**, не ML/generative AI. Причина — прямое требование ТЗ к результату («корректный, воспроизводимый и объяснимый») и отдельный критерий оценки «трассируемость посадка → норма → пункт»: правило вида «отступ X м, источник — 743-ПП п. Y» проверяемо человеком напрямую, в отличие от вывода обученной модели. ML рассматривался как возможная вторая итерация (`docs/ARCHITECTURE.md`), но не потребовался для прохождения критериев MVP.

## Библиотеки (Python), версии зафиксированы точно

| Библиотека | Версия | Назначение | Источник |
|---|---|---|---|
| `ezdxf` | 1.4.4 | Чтение/запись DXF (Ingest, DXF Export) | [pypi.org/project/ezdxf](https://pypi.org/project/ezdxf/) |
| `shapely` | 2.1.2 | Геометрия: буферы отступов, полигоны допустимых зон, пересечения | [pypi.org/project/shapely](https://pypi.org/project/shapely/) (обёртка над GEOS) |
| `pyyaml` | 6.0.3 | Загрузка справочников норм (YAML) | [pypi.org/project/PyYAML](https://pypi.org/project/PyYAML/) |
| `openpyxl` | 3.1.5 | Разбор Excel (прайс-листы питомников) | [pypi.org/project/openpyxl](https://pypi.org/project/openpyxl/) |
| `python-docx` | 1.2.0 | Разбор DOCX (нормативные акты в исходном формате, прайс-листы) | [pypi.org/project/python-docx](https://pypi.org/project/python-docx/) |
| `pymupdf` | 1.27.2.3 | Разбор PDF (нормативные акты, прайс-листы) | [pypi.org/project/PyMuPDF](https://pypi.org/project/PyMuPDF/) |
| `jinja2` | 3.1.6 | Шаблонизация человекочитаемого отчёта (Markdown + HTML, `pipeline/report/human_report.py`) | [pypi.org/project/Jinja2](https://pypi.org/project/Jinja2/) |
| `matplotlib` | 3.10.8 | Рендер схем «до/после» для человекочитаемого отчёта (headless, `matplotlib.use("Agg")`) — только отрисовка уже посчитанных геометрий, не участвует в геометрических расчётах и не влияет на воспроизводимость пайплайна | [pypi.org/project/matplotlib](https://pypi.org/project/matplotlib/) |
| `pytest` | 9.0.2 | Тесты | [pypi.org/project/pytest](https://pypi.org/project/pytest/) |
| `fastapi` | 0.135.1 | Веб-UI backend, Swagger/OpenAPI «из коробки» на `/docs` | [pypi.org/project/fastapi](https://pypi.org/project/fastapi/) |
| `uvicorn` | 0.41.0 | ASGI-сервер для FastAPI | [pypi.org/project/uvicorn](https://pypi.org/project/uvicorn/) |
| `python-multipart` | 0.0.22 | Приём файлов (`UploadFile`) в FastAPI | [pypi.org/project/python-multipart](https://pypi.org/project/python-multipart/) |
| `httpx` | 0.28.1 | Только для тестов (`TestClient`) | [pypi.org/project/httpx](https://pypi.org/project/httpx/) |

**Системная зависимость** (не pip-пакет): GEOS 3.13.1 — движок геометрии, на котором построен `shapely`. Версия зафиксирована и указана в `Dockerfile`, так как разные версии GEOS могут по-разному считать буферы (это напрямую влияет на обязательное требование заказчика к повторяемости результата, см. `tests/test_reproducibility.py`).

**Внешний исполняемый файл** (не pip-пакет): ODA File Converter — конвертация DWG→DXF (`pipeline/ingest/dwg_convert.py`). Лицензионно закрыт EULA-формой на сайте ODA, поэтому не входит в `requirements.txt` и не запекается автоматически в Docker-образ — устанавливается отдельно (см. `docker/oda/README.md`).

## Источники нормативных данных

Полный реестр — `docs/NORMATIVE_REFERENCES.md`. Основные акты: 743-ПП (отступы от коммуникаций, Таблица 3.6.1), 623-ПП/МГСН 1.02-02 (густота посадки, ассортимент по территориям), 369-ПП (инвазивные виды), Постановление Правительства РФ №160 (охранные зоны ЛЭП), СП 42.13330.2016 (градостроительные нормы), СП 82.13330.2016 (благоустройство), ГОСТ 21.508-2020 (оформление документации генпланов), официальные каталоги ДПиООС mos.ru (основной ассортимент, перспективные виды растений).
