# Как обновить нормативную базу

Прямой запрос пользователя (2026-09-19): сервис должен уметь работать с
обновлённой нормативной базой без правки кода. Решение (согласовано с
пользователем): **ручная замена файла** в `data/reference/`, сервис
защищается от ошибок при такой замене валидацией структуры (см.
`pipeline/common/schema_validation.py`).

## Как это работает технически

Каждый реестр (класс вроде `DpioosSpeciesCatalog`, `OffsetRegistry` и т.д.)
читает свой файл с диска **заново при каждом создании объекта** — кэша нет.
В веб-UI (`pipeline/web/app.py`) реестр создаётся заново на каждый HTTP-запрос
— значит, замена файла на диске подхватывается **сразу, без перезапуска
сервиса**. В CLI (`pipeline/run.py`, `pipeline/review_run.py`) — на следующий
запуск команды.

При загрузке проверяется структура **верхнего уровня** файла (обязательные
поля и их тип) — если что-то не так, сервис упадёт понятной ошибкой
(`DataValidationError`) с указанием файла и поля, а не случайным `KeyError`.
Это защита от «подменили не тот файл» — не замена ручной сверки нового
содержимого с первоисточником акта.

Какая версия каждого файла сейчас используется — видно в UI (кнопка
«ℹ️ Нормативная база» в шапке) или через `GET /api/data-sources` — там дата
последнего изменения файла на диске.

## Таблица: файл → акт → обязательные поля верхнего уровня

| Файл | Акт | Обязательные поля | Загружает |
|---|---|---|---|
| `dpioos_species_assortment.json` | ДПиООС mos.ru (осн. ассортимент + перспективные виды) | `source_documents` (объект), `territory_categories` (объект), `footnote_legend` (объект), `species` (массив) | `pipeline/catalog/dpioos_species_catalog.py` |
| `species_territory_recommendations.json` | 623-ПП, Табл. В.6 (вторичная сверка) | `source` (объект), `species` (массив) | `pipeline/catalog/species_territory_recommendations.py` |
| `invasive_species.json` | 369-ПП, Приложение 1 | `source` (объект), `prohibition_clause` (строка), `species` (массив) | `pipeline/catalog/invasive_species.py` |
| `offset_norms.yaml` | 743-ПП, Табл. 3.6.1 | `source` (объект), `offsets` (массив) | `pipeline/constraints/offset_registry.py` |
| `lep_protection_zones.yaml` | ПП РФ №160 | `source` (объект), `overhead_lines` (массив), `underground_cable_lines` (объект) | `pipeline/constraints/lep_zones.py` |
| `planting_density_per_hectare.yaml` | 623-ПП, Табл. В.1 | `density_per_ha` (объект) | `pipeline/placement/density.py` |
| `existing_vegetation_buffer_policy.yaml` | НЕ норма — допущение проекта | `vigor_tiers` (объект), `unknown_vigor_default_tier` (строка), `rationale` (строка) | `pipeline/constraints/existing_vegetation.py` |
| `planting_cost_estimates.yaml` | НЕ норма — рыночная оценка | `target_budget_per_hectare` (объект), `categories` (объект) | `pipeline/catalog/planting_cost.py` |
| `layer_name_aliases.yaml` | Инженерная конвенция (не норма) | только структура верхнего уровня — словарь | `pipeline/ingest/layer_classifier.py` |

## Как безопасно заменить файл

1. Сохраните новый файл под тем же именем и в том же месте (`data/reference/<имя>`), сохранив top-level структуру из таблицы выше.
2. Если запускаете локально/в Docker — ничего перезапускать не нужно (веб-UI подхватит сразу на следующий запрос).
3. Проверьте, что сервис не упал: откройте `/api/data-sources` — если новый файл прошёл валидацию, там появится свежая дата обновления. Если файл повреждён — любой эндпоинт, использующий этот реестр, ответит 500 с текстом `DataValidationError`, где явно назван файл и отсутствующее/неверное поле.
4. **Обязательно прогоните `pytest tests/ -q`.** Валидация здесь проверяет только структуру, а не содержимое — если новый файл меняет состав видов/чисел по существу, часть тестов содержит жёсткие проверки по значению текущих данных (например, `tests/test_dpioos_species_catalog.py`: `assert len(entries) > 300`, точный `catalog.lookup("Ель колючая (формы и сорта)")`) — их придётся пересмотреть вручную, это не решается автоматически.
