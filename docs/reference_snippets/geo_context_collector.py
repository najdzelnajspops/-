"""
Предварительный сбор «геоконтекста» для всех объявлений с координатами.

Один Overpass union-запрос покрывает ВСЕ 4 группы данных за 1 вызов API:
  fz171      — объекты под ограничения ФЗ-171 / ПП РФ №1425 (радиус 150 м)
  transport  — метро (1000 м) + остановки (500 м)
  footfall   — жилые дома (300 м), офисы (500 м), ТЦ (500 м)
  competition— конкуренты по категориям (500 м)

Результат → data/cache/geo_context.db, таблица geo_context.

Использование:
  python scripts/geo_context_collector.py              # только новые (без контекста)
  python scripts/geo_context_collector.py --force      # перепроверить все
  python scripts/geo_context_collector.py --stats      # статистика
  python scripts/geo_context_collector.py --limit 20   # тест на 20 объявлениях
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_GEO_DB   = _ROOT / "data" / "cache" / "geo_context.db"
_CACHE_DBS = {
    "cian":  _ROOT / "data" / "cache" / "cian_cache.db",
    "avito": _ROOT / "data" / "cache" / "avito_cache.db",
}

_PAUSE_SEC = 3.5          # пауза между запросами (Overpass public limit ≈ 1 req/3 s)
_TIMEOUT   = 60           # таймаут одного Overpass-запроса, сек
_MAX_RETRIES = 3          # попыток на один объект
_COOLDOWN_AFTER = 5       # пауза на охлаждение после N ошибок подряд
_COOLDOWN_SEC   = 300     # длина паузы (5 мин)

# Публичные зеркала Overpass API — переключаемся по кругу при 429/504
_OVERPASS_ENDPOINTS = [
    "https://overpass.openstreetmap.ru/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
_endpoint_idx = 0         # текущий эндпоинт
_proxy_url: str | None = None  # HTTP proxy, напр. "http://192.168.0.5:8080"


def _next_endpoint() -> str:
    global _endpoint_idx
    _endpoint_idx = (_endpoint_idx + 1) % len(_OVERPASS_ENDPOINTS)
    return _OVERPASS_ENDPOINTS[_endpoint_idx]


def _current_endpoint() -> str:
    return _OVERPASS_ENDPOINTS[_endpoint_idx]

# ──────────────────────────────────────────────────────────────────────────────
# Overpass union-запрос — все 4 группы за один вызов
# Используем {lat} / {lng} как placeholders (format-строка вызовется позже)
# ──────────────────────────────────────────────────────────────────────────────
_QUERY_TEMPLATE = """
[out:json][timeout:{timeout}];
(
  /* ── ФЗ-171 / ПП РФ №1425: запрещённые объекты, 150 м ─────────────────── */
  node(around:150,{lat},{lng})[amenity~"^(school|college|university|hospital|clinic|doctors|pharmacy|place_of_worship|library|theatre|cinema|sports_centre|stadium|swimming_pool)$"];
  way (around:150,{lat},{lng})[amenity~"^(school|college|university|hospital|clinic|doctors|pharmacy|place_of_worship|library|theatre|cinema|sports_centre|stadium|swimming_pool)$"];
  node(around:150,{lat},{lng})[leisure~"^(sports_centre|stadium|swimming_pool|fitness_centre|sports_hall|ice_rink)$"];
  way (around:150,{lat},{lng})[leisure~"^(sports_centre|stadium|swimming_pool|fitness_centre|sports_hall|ice_rink)$"];
  /* Салоны красоты — отдельное предупреждение (не нарушение) */
  node(around:150,{lat},{lng})[shop="beauty"];
  way (around:150,{lat},{lng})[shop="beauty"];

  /* ── Транспорт: метро 1000 м ─────────────────────────────────────────── */
  node(around:1000,{lat},{lng})[station="subway"];
  node(around:1000,{lat},{lng})[railway="station"][subway="yes"];
  node(around:1000,{lat},{lng})[railway="subway_entrance"];
  /* Остановки 500 м */
  node(around:500,{lat},{lng})[highway="bus_stop"];
  node(around:500,{lat},{lng})[amenity="bus_station"];

  /* ── Поток: жилые дома 300 м ─────────────────────────────────────────── */
  way(around:300,{lat},{lng})[building~"^(residential|apartments|house|dormitory)$"];
  /* Офисы 500 м */
  node(around:500,{lat},{lng})[office];
  way (around:500,{lat},{lng})[building="office"];
  way (around:500,{lat},{lng})[office];
  /* ТЦ / торговля 500 м */
  node(around:500,{lat},{lng})[shop="mall"];
  way (around:500,{lat},{lng})[shop="mall"];
  way (around:500,{lat},{lng})[building~"^(retail|supermarket|mall)$"];
  node(around:500,{lat},{lng})[amenity="marketplace"];

  /* ── Конкуренция: общепит 500 м ─────────────────────────────────────── */
  node(around:500,{lat},{lng})[amenity~"^(restaurant|cafe|bar|fast_food|food_court|pub|biergarten)$"];
  way (around:500,{lat},{lng})[amenity~"^(restaurant|cafe|bar|fast_food|food_court|pub|biergarten)$"];
  /* Продуктовый ритейл */
  node(around:500,{lat},{lng})[shop~"^(supermarket|convenience|grocery|greengrocer|deli|butcher|seafood|bakery)$"];
  way (around:500,{lat},{lng})[shop~"^(supermarket|convenience|grocery|greengrocer|deli|butcher|seafood|bakery)$"];
  /* Одежда / fashion retail */
  node(around:500,{lat},{lng})[shop~"^(clothes|shoes|bag|boutique|fashion_accessories|jewelry)$"];
  way (around:500,{lat},{lng})[shop~"^(clothes|shoes|bag|boutique|fashion_accessories|jewelry)$"];
  /* Салоны красоты */
  node(around:500,{lat},{lng})[shop="beauty"];
  way (around:500,{lat},{lng})[shop="beauty"];
  node(around:500,{lat},{lng})[beauty];
  way (around:500,{lat},{lng})[beauty];
  /* Общий ритейл */
  node(around:500,{lat},{lng})[shop];
  way (around:500,{lat},{lng})[shop];
);
out center;
""".strip()

# ──────────────────────────────────────────────────────────────────────────────
# Теги: к какой группе относится элемент
# ──────────────────────────────────────────────────────────────────────────────
_FZ171_AMENITY = {
    "school", "college", "university",
    "hospital", "clinic", "doctors", "pharmacy",
    "place_of_worship", "library",
    "theatre", "cinema",
    "sports_centre", "stadium", "swimming_pool",
}
_FZ171_LEISURE = {
    "sports_centre", "stadium", "swimming_pool",
    "fitness_centre", "sports_hall", "ice_rink",
}
_METRO_STATION = {"station", "railway"}   # по тегу station=subway или railway=station+subway=yes
_METRO_ENTRANCE = "subway_entrance"
_BUS_STOP    = {"bus_stop", "bus_station"}
_RESIDENTIAL = {"residential", "apartments", "house", "dormitory"}
_OFFICE_AMN  = {"office"}  # amenity/office tag present
_RETAIL_BLD  = {"retail", "supermarket", "mall"}
_MALL_SHOP   = {"mall"}
_FOOD_AMN    = {"restaurant", "cafe", "bar", "fast_food", "food_court", "pub", "biergarten"}
_GROCERY_SHOP = {"supermarket", "convenience", "grocery", "greengrocer", "deli", "butcher", "seafood", "bakery"}
_CLOTHES_SHOP = {"clothes", "shoes", "bag", "boutique", "fashion_accessories", "jewelry"}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _make_opener():
    """Создаёт urllib opener — с прокси или без."""
    if _proxy_url:
        proxy = urllib.request.ProxyHandler({"http": _proxy_url, "https": _proxy_url})
        return urllib.request.build_opener(proxy)
    return urllib.request.build_opener()


def _query_overpass(lat: float, lng: float) -> list[dict]:
    query = _QUERY_TEMPLATE.format(lat=lat, lng=lng, timeout=_TIMEOUT)
    data  = urllib.parse.urlencode({"data": query}).encode()
    opener = _make_opener()

    last_exc: Exception = RuntimeError("no attempts")
    for attempt in range(_MAX_RETRIES):
        url = _current_endpoint()
        try:
            req = urllib.request.Request(
                url, data=data,
                headers={"User-Agent": "greening-planner/1.0"},
            )
            with opener.open(req, timeout=_TIMEOUT + 10) as resp:
                return json.loads(resp.read())["elements"]
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code in (403, 429, 500, 502, 503, 504):
                wait = 20 * (attempt + 1)   # 20 / 40 / 60 сек
                print(f"      [{e.code}] {url} — ждём {wait} с, переключаем эндпоинт...")
                _next_endpoint()
                time.sleep(wait)
            else:
                raise
        except Exception as e:
            last_exc = e
            wait = 10 * (attempt + 1)
            print(f"      [err] {e} — ждём {wait} с, переключаем эндпоинт...")
            _next_endpoint()
            time.sleep(wait)

    raise last_exc


def _el_coords(el: dict) -> tuple[float, float] | None:
    lat = el.get("lat") or (el.get("center") or {}).get("lat")
    lng = el.get("lon") or (el.get("center") or {}).get("lon")
    if lat and lng:
        return float(lat), float(lng)
    return None


def _el_name(el: dict) -> str:
    tags = el.get("tags", {})
    return tags.get("name:ru") or tags.get("name") or ""


def _build_context(lat: float, lng: float, elements: list[dict]) -> dict:
    """Разбирает элементы Overpass на 4 группы и строит context_json."""

    # ── ФЗ-171 ────────────────────────────────────────────────────────────────
    fz171_hits: list[tuple[str, float]] = []   # (name, dist_m)
    beauty_warnings = 0
    for el in elements:
        coords = _el_coords(el)
        if not coords:
            continue
        dist = _haversine_m(lat, lng, *coords)
        if dist > 150:
            continue
        tags = el.get("tags", {})
        amenity = tags.get("amenity", "")
        leisure = tags.get("leisure", "")
        shop    = tags.get("shop", "")

        if amenity in _FZ171_AMENITY or leisure in _FZ171_LEISURE:
            fz171_hits.append((_el_name(el) or amenity or leisure, dist))
        elif shop == "beauty":
            beauty_warnings += 1

    violations    = len(fz171_hits)
    nearest_name  = ""
    nearest_m     = None
    if fz171_hits:
        fz171_hits.sort(key=lambda x: x[1])
        nearest_name, nearest_m = fz171_hits[0]
        nearest_m = round(nearest_m)

    # ── Транспорт ─────────────────────────────────────────────────────────────
    metro_candidates: list[tuple[str, float]] = []  # (name, dist_m)
    bus_count = 0
    for el in elements:
        coords = _el_coords(el)
        if not coords:
            continue
        dist = _haversine_m(lat, lng, *coords)
        tags = el.get("tags", {})
        railway = tags.get("railway", "")
        station = tags.get("station", "")
        highway = tags.get("highway", "")
        amenity = tags.get("amenity", "")

        is_metro = (
            station == "subway"
            or (railway == "station" and tags.get("subway") == "yes")
            or railway == _METRO_ENTRANCE
        )
        if is_metro and dist <= 1000:
            metro_candidates.append((_el_name(el), dist))
        elif (highway == "bus_stop" or amenity == "bus_station") and dist <= 500:
            bus_count += 1

    metro_m    = None
    metro_name = ""
    if metro_candidates:
        metro_candidates.sort(key=lambda x: x[1])
        metro_name, metro_m = metro_candidates[0]
        metro_m = round(metro_m)

    # ── Поток ─────────────────────────────────────────────────────────────────
    residential_300m = 0
    offices_500m     = 0
    malls_500m       = 0
    seen_ways: set[int] = set()  # избегаем двойного счёта way с тегами office+building

    for el in elements:
        coords = _el_coords(el)
        if not coords:
            continue
        dist = _haversine_m(lat, lng, *coords)
        tags    = el.get("tags", {})
        el_id   = el.get("id", 0)
        building = tags.get("building", "")
        office   = tags.get("office", "")
        shop     = tags.get("shop", "")

        if building in _RESIDENTIAL and dist <= 300:
            residential_300m += 1
        if dist <= 500:
            if (office or building == "office") and el_id not in seen_ways:
                offices_500m += 1
                seen_ways.add(el_id)
            if shop in _MALL_SHOP or building in _RETAIL_BLD:
                malls_500m += 1

    # ── Конкуренция ───────────────────────────────────────────────────────────
    food_500m = 0
    grocery_500m = 0
    clothes_500m = 0
    beauty_500m = 0
    retail_500m = 0
    for el in elements:
        coords = _el_coords(el)
        if not coords:
            continue
        dist = _haversine_m(lat, lng, *coords)
        if dist > 500:
            continue
        tags = el.get("tags", {})
        amenity = tags.get("amenity", "")
        shop = tags.get("shop", "")
        beauty = tags.get("beauty", "")
        if amenity in _FOOD_AMN:
            food_500m += 1
        if shop in _GROCERY_SHOP:
            grocery_500m += 1
        if shop in _CLOTHES_SHOP:
            clothes_500m += 1
        if shop == "beauty" or beauty:
            beauty_500m += 1
        if shop:
            retail_500m += 1

    return {
        "fz171": {
            "violations":         violations,
            "nearest_name":       nearest_name,
            "nearest_m":          nearest_m,
            "warning_zone_count": beauty_warnings,
        },
        "transport": {
            "metro_m":       metro_m,
            "metro_name":    metro_name,
            "bus_stops_500m": bus_count,
        },
        "footfall": {
            "residential_300m": residential_300m,
            "offices_500m":     offices_500m,
            "malls_500m":       malls_500m,
        },
        "competition": {
            "food_service_500m": food_500m,
            "grocery_500m": grocery_500m,
            "clothes_500m": clothes_500m,
            "beauty_500m": beauty_500m,
            "retail_shops_500m": retail_500m,
        },
        "collected_at": str(date.today()),
    }


# ──────────────────────────────────────────────────────────────────────────────
# SQLite — хранилище контекста
# ──────────────────────────────────────────────────────────────────────────────

def _init_geo_db() -> sqlite3.Connection:
    _GEO_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(_GEO_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS geo_context (
            source       TEXT NOT NULL,
            listing_id   TEXT NOT NULL,
            context_json TEXT,
            collected_at TEXT DEFAULT (date('now')),
            PRIMARY KEY (source, listing_id)
        )
    """)
    con.commit()
    return con


def _load_already_collected(con: sqlite3.Connection) -> set[tuple[str, str]]:
    # Считаем «уже обработанными» только те, где context_json IS NOT NULL.
    # Записи с NULL (ошибки прошлых запусков) автоматически попадут в очередь.
    cur = con.execute("SELECT source, listing_id FROM geo_context WHERE context_json IS NOT NULL")
    return {(r[0], r[1]) for r in cur.fetchall()}


def _collect_listings(force: bool, collected: set) -> list[dict]:
    rows = []
    for source, db_path in _CACHE_DBS.items():
        if not db_path.exists():
            continue
        try:
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            cur = c.cursor()
            cur.execute(
                "SELECT listing_id, address, lat, lng FROM listings "
                "WHERE lat IS NOT NULL AND lat != 0 AND lng IS NOT NULL AND lng != 0"
            )
            for r in cur.fetchall():
                lid = str(r["listing_id"])
                if not force and (source, lid) in collected:
                    continue
                rows.append({
                    "source":     source,
                    "listing_id": lid,
                    "address":    r["address"] or "",
                    "lat":        float(r["lat"]),
                    "lng":        float(r["lng"]),
                })
            c.close()
        except Exception as e:
            print(f"  ⚠  Ошибка чтения {db_path.name}: {e}")
    return rows


# ──────────────────────────────────────────────────────────────────────────────
# Основной цикл
# ──────────────────────────────────────────────────────────────────────────────

def _run(force: bool = False, limit: int = 0) -> None:
    con       = _init_geo_db()
    collected = _load_already_collected(con)
    listings  = _collect_listings(force, collected)
    if limit:
        listings = listings[:limit]

    total = len(listings)
    if total == 0:
        print("Нечего собирать — все объявления уже обработаны (или нет координат).")
        print("Используйте --force для пересбора.")
        con.close()
        return

    est_min = round(total * _PAUSE_SEC / 60)
    print(f"Собираем геоконтекст для {total} объявлений (пауза {_PAUSE_SEC} с)...")
    print(f"Ориентировочное время: ~{est_min} мин.\n")

    ok = err = 0
    consecutive_err = 0
    for i, item in enumerate(listings, 1):
        lat, lng = item["lat"], item["lng"]
        src, lid = item["source"], item["listing_id"]
        addr = item["address"][:60]

        try:
            elements = _query_overpass(lat, lng)
            ctx      = _build_context(lat, lng, elements)
            ctx_json = json.dumps(ctx, ensure_ascii=False)

            con.execute(
                "INSERT OR REPLACE INTO geo_context (source, listing_id, context_json, collected_at)"
                " VALUES (?, ?, ?, date('now'))",
                (src, lid, ctx_json),
            )
            con.commit()

            fz  = ctx["fz171"]
            tr  = ctx["transport"]
            fl  = ctx["footfall"]
            cp  = ctx["competition"]
            metro_str = f"🚇{tr['metro_m']}м" if tr["metro_m"] else "🚇—"
            viol_str  = f"⚠ ФЗ-171:{fz['violations']}@{fz['nearest_m']}м" if fz["violations"] else "✅ФЗ-171:ok"
            print(
                f"[{i}/{total}] {viol_str}  {metro_str}  "
                f"🏘{fl['residential_300m']} 🏢{fl['offices_500m']} 🍽{cp['food_service_500m']}  "
                f"{addr}"
            )
            ok += 1
            consecutive_err = 0  # сбрасываем счётчик при успехе

        except Exception as e:
            err += 1
            consecutive_err += 1
            print(f"[{i}/{total}] ⚠  {addr} — ошибка: {e}")
            try:
                con.execute(
                    "INSERT OR REPLACE INTO geo_context (source, listing_id, context_json, collected_at)"
                    " VALUES (?, ?, NULL, date('now'))",
                    (src, lid),
                )
                con.commit()
            except Exception as db_err:
                print(f"      [db] Не удалось сохранить запись об ошибке: {db_err}")

            # Охлаждение: если N ошибок подряд — ждём 5 мин и меняем эндпоинт
            if consecutive_err >= _COOLDOWN_AFTER:
                print(f"\n  ⏸  {consecutive_err} ошибок подряд — охлаждение {_COOLDOWN_SEC // 60} мин...\n")
                _next_endpoint()
                time.sleep(_COOLDOWN_SEC)
                consecutive_err = 0

        if i < total:
            time.sleep(_PAUSE_SEC)

    con.close()
    print(f"\nГотово: ✅ {ok} собрано  ⚠ {err} ошибок")


def _stats() -> None:
    if not _GEO_DB.exists():
        print("База geo_context.db ещё не создана. Запустите без --stats.")
        return
    con = sqlite3.connect(str(_GEO_DB))
    total,  = con.execute("SELECT COUNT(*) FROM geo_context").fetchone()
    ok,     = con.execute("SELECT COUNT(*) FROM geo_context WHERE context_json IS NOT NULL").fetchone()
    errors, = con.execute("SELECT COUNT(*) FROM geo_context WHERE context_json IS NULL").fetchone()
    last,   = con.execute("SELECT MAX(collected_at) FROM geo_context").fetchone()
    con.close()
    print(f"Статистика geo_context.db (последнее обновление: {last or '—'}):")
    print(f"  Всего записей : {total}")
    print(f"  ✅ С контекстом: {ok}")
    print(f"  ⚠  Ошибки     : {errors}")


if __name__ == "__main__":
    sys.path.insert(0, str(_ROOT))

    parser = argparse.ArgumentParser(description="Сбор геоконтекста из Overpass API")
    parser.add_argument("--force",  action="store_true", help="Пересобрать все")
    parser.add_argument("--stats",  action="store_true", help="Показать статистику")
    parser.add_argument("--limit",  type=int, default=0,  help="Ограничить N объявлений")
    parser.add_argument("--proxy",  type=str, default=None, help="HTTP прокси, напр. http://192.168.0.5:8080")
    args = parser.parse_args()

    if args.proxy:
        _proxy_url = args.proxy
        print(f"Прокси: {_proxy_url}")

    if args.stats:
        _stats()
    else:
        _run(force=args.force, limit=args.limit)
