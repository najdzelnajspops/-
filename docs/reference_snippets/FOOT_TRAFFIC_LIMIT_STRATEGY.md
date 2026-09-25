# FOOT-TRAFFIC / GEO ENRICHMENT LIMIT STRATEGY

## Зачем нужен этот план

Этот документ фиксирует рабочую архитектуру проекта для `geo / foot-traffic / places / parking` слоёв.

Главная цель:

- не жечь лимиты на повторяющихся адресах и одинаковых координатах;
- не смешивать геокодирование объекта с enrichment окружения;
- держать runtime быстрым и дешёвым;
- переводить удачные внешние ответы в локальный reusable cache/reference layer.

Москва остаётся стартовым сложным регионом, но сам контур проектируется как всерегиональный.

## Где мы сейчас реально находимся

### Уже есть и работает

1. `listing`-кэши по источникам в SQLite.
2. reusable building/address geo layer: `data/cache/building_geo_cache.db`.
3. offline геоконтекст по Overpass: `data/cache/geo_context.db`.
4. provider-chain для геокодинга: `config/geocoding.yaml`.

### Чего пока не хватает

1. Явного разделения между:
   - `address_resolution`
   - `location_enrichment`
2. Отдельного versioned cache-layer для:
   - geocode queries
   - places queries
   - final score results
3. Формализованной лимитной политики:
   - какие провайдеры идут первыми
   - где нужен batch
   - где нужен shortlist-only режим
4. Offline import-контура для внешних reference-слоёв.

Вывод: проект уже не на нулевом этапе. Фундамент есть. Сейчас нужен не “ещё один API”, а нормальная дисциплина слоёв и лимитов.

## Ключевой принцип

Всегда идти в таком порядке:

1. `local cache / local registry`
2. `address repair / normalization`
3. `external geocoding`
4. `places enrichment`
5. `routing / distance matrix`
6. `final score cache`

Нельзя начинать с дорогих провайдеров там, где локальный слой уже способен закрыть кейс.

## Архитектура слоёв

### Layer A. Address Resolution

Назначение: получить точку объекта или здания.

Источники по приоритету:

1. `listing lat/lng` из источника
2. `building_geo_cache`
3. локальная нормализация адреса
4. `DaData` для repair/normalization only
5. `2GIS / Google / Yandex` для unresolved cases
6. `Nominatim` только как very-last fallback

Правило: если дом уже был уверенно геокодирован раньше, повторный внешний вызов запрещён.

### Layer B. Location Enrichment

Назначение: собрать вокруг уже известной точки признаки окружения.

Источники по приоритету:

1. локальные offline reference datasets
2. `Overpass` union-queries
3. `2GIS Places`
4. `Google Places`
5. дополнительные региональные/городские open datasets

Правило: этот слой не должен заново решать адрес объекта. Он работает только от координат.

### Layer C. Routing / Access

Назначение: уточнить не “что рядом”, а “насколько это доступно”.

Правило: `Distance Matrix` и любые routing API нельзя ставить в ранний массовый контур.

Они используются только:

1. для shortlist;
2. для premium enrichment;
3. для спорных кейсов после already-built nearby-layer.

## Провайдерная стратегия

### DaData

Роль: `address repair / normalization layer`.

Используем когда:

- адрес шумный;
- строка требует нормализации;
- локальный parser не справился.

Не используем когда:

- уже есть нормальный `building_geo` hit;
- уже есть хорошие координаты;
- нужно массово считать nearby POI или footfall.

### 2GIS

Роль: основной внешний слой для:

- places / organizations
- business centers
- named commercial entities
- category-rich nearby search

Лимитная политика:

- сначала deduplicated batch;
- потом cache by rounded coords + radius;
- массовый `Distance Matrix` запрещён вне shortlist.

### Google

Роль: сильный fallback для:

- сложных address geocoding cases;
- complex/business-center like entities;
- weak or missing place results после 2GIS.

Лимитная политика:

- не первая линия по всему потоку;
- bounded queues;
- только после local/2GIS слоёв.

### Overpass / OSM

Роль: бесплатный offline-style слой для:

- footfall approximation
- transport anchors
- competition counts
- regulatory context

Лимитная политика:

- не runtime for every request;
- только precompute / refresh jobs;
- строгие паузы и endpoint rotation.

### data.mos.ru / city datasets

Роль: не runtime API, а `offline reference import`.

Лучший способ экономии лимитов здесь — не API-вызовы, а редкий offline refresh и локальный spatial join.

## Кэш-стратегия

### Почему SQLite, а не Redis

Сейчас основной кэш должен быть в `SQLite`, потому что:

1. проект уже живёт на SQLite;
2. данные нужно дебажить и переиспользовать между запусками;
3. важна воспроизводимость;
4. shared ephemeral in-memory cache пока не нужен.

### Обязательные типы кэша

1. `Geocode Cache`
2. `Places Cache`
3. `Score Cache`

### Versioned cache keys

Плохо:

`foottraffic_{lat}_{lng}_{radius}`

Правильно:

`foottraffic:v2:{lat_round}:{lng_round}:{radius}:{providers_hash}:{scoring_version}`

Иначе старая схема, старый provider-set и новая формула будут смешиваться в одном cache-hit.

### TTL и invalidation

- `geocode cache`: длинный срок жизни, скорее version-based refresh
- `places cache`: типичный TTL `30 days`
- `score cache`: TTL `30 days` + bust по `scoring_version`

## Batch-политика

### Что можно batch-ить

1. unresolved address queues
2. deduplicated places enrichment queues
3. shortlist routing queues
4. offline geo-context refresh

### Что нельзя batch-ить без фильтра

1. весь listing corpus в дорогие geocoder API
2. весь corpus в distance-matrix
3. все noisy addresses в DaData “на всякий случай”

## Этапы реализации

### Этап 1. Фиксация архитектуры и кэша

Задачи:

1. зафиксировать этот план в правилах проекта;
2. добавить отдельный versioned `location_enrichment_cache`;
3. формализовать cache key policy;
4. добавить stats/smoke entrypoints;
5. покрыть это тестами.

Статус: начинаем сейчас.

### Этап 2. Разделение пайплайнов

Задачи:

1. выделить отдельный geocode service/helper;
2. оставить `DaData` только в repair-layer;
3. ограничить Google/2GIS для unresolved tails;
4. перевести heavy enrichment в offline jobs.

Статус: следующий этап после cache-layer.

### Этап 3. Offline reference layers

Задачи:

1. импорт parking/reference datasets;
2. локальный spatial join по координатам;
3. versioned refresh scripts.

Статус: пока не начат.

### Этап 4. Places enrichment

Задачи:

1. deduplicated queue by rounded coords;
2. `2GIS Places` как основной внешний слой;
3. `Google Places` как fallback;
4. сохранение результатов в `places_cache`.

Статус: не начат.

### Этап 5. Routing / shortlist

Задачи:

1. shortlist-only queue;
2. matrix/routing не раньше этого этапа;
3. write-through в score cache.

Статус: не начат.

## Практические инварианты проекта

1. Никакой дорогой внешний запрос не делается без попытки local cache hit.
2. Один и тот же дом не должен геокодироваться повторно, если уже есть уверенная building-level точка.
3. Один и тот же coordinate+radius кейс не должен заново собирать nearby places, если TTL ещё жив.
4. Runtime search/report не должен зависеть от массовых live API-вызовов.
5. Внешние провайдеры — это enrichment tail, а не первая линия обработки.

## Следующий технический шаг

1. ввести отдельный `location_enrichment_cache` в SQLite;
2. добавить versioned keys для geocode / places / score;
3. дать ему smoke/stats entrypoint;
4. только потом подключать новые provider adapters.
