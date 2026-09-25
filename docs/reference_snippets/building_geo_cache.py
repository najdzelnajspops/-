from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "cache" / "building_geo_cache.db"

_DDL = """
CREATE TABLE IF NOT EXISTS building_geo (
    building_key       TEXT PRIMARY KEY,
    normalized_address TEXT NOT NULL UNIQUE,
    city               TEXT,
    street_name        TEXT,
    street_type        TEXT,
    house              TEXT,
    block              TEXT,
    building           TEXT,
    lat                REAL NOT NULL,
    lng                REAL NOT NULL,
    geo_source         TEXT NOT NULL,
    geo_confidence     TEXT NOT NULL,
    source_address_raw TEXT,
    first_seen_at      TEXT NOT NULL,
    last_seen_at       TEXT NOT NULL,
    use_count          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_building_geo_city
    ON building_geo (city);
CREATE INDEX IF NOT EXISTS idx_building_geo_street_house
    ON building_geo (street_name, house);
"""

_RE_METRO_STRIP = re.compile(
    r",?\s*"
    r"[А-ЯЁA-Z][А-ЯЁA-Zа-яёa-z\\-]+"
    r"(?:\s+[А-ЯЁA-Zа-яёa-z][А-ЯЁA-Zа-яёa-z\\-]+)*"
    r",\s*(?:(?:до|от)\s+)?\d+[–—\\-]?\d*\s*мин\.?\s*$",
    re.UNICODE,
)

_ADDR_ABBR = [
    (re.compile(r"\bпр-т\b", re.I | re.U), "проспект"),
    (re.compile(r"\bпр-д\b", re.I | re.U), "проезд"),
    (re.compile(r"\bпр\.", re.I | re.U), "проезд"),
    (re.compile(r"\bб-р\b", re.I | re.U), "бульвар"),
    (re.compile(r"\bнаб\.", re.I | re.U), "набережная"),
    (re.compile(r"\bпер\.", re.I | re.U), "переулок"),
    (re.compile(r"\bш\.", re.I | re.U), "шоссе"),
    (re.compile(r"\bул\.", re.I | re.U), "улица"),
    (re.compile(r"\bпл\.", re.I | re.U), "площадь"),
]

_STREET_TYPES = [
    "улица",
    "проспект",
    "проезд",
    "бульвар",
    "набережная",
    "переулок",
    "шоссе",
    "площадь",
    "аллея",
    "тупик",
    "линия",
    "квартал",
    "микрорайон",
    "территория",
    "поселок",
    "деревня",
]


@dataclass
class NormalizedBuildingAddress:
    building_key: str
    normalized_address: str
    city: str
    street_name: str
    street_type: str
    house: str
    block: str
    building: str


def open_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript(_DDL)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _expand_abbr(address: str) -> str:
    for pat, full in _ADDR_ABBR:
        address = pat.sub(full, address)
    return address


def _collapse_spaces(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "", flags=re.U)
    return text.strip(" ,")


def _normalize_city_prefix(address: str, city: str) -> str:
    address = re.sub(r"^\s*г\.\s*", "", address, flags=re.I | re.U)
    if address.lower().startswith(city.lower() + ","):
        return address
    if address.lower() == city.lower():
        return city
    return f"{city}, {address}" if address else city


def _split_house_tokens(house_part: str) -> tuple[str, str, str]:
    house_part = _collapse_spaces(house_part)
    house = house_part
    block = ""
    building = ""

    m = re.search(r"\b(?:к|корпус)\s*([0-9А-ЯA-Zа-яa-z\-]+)\b", house_part, re.I | re.U)
    if m:
        block = m.group(1)

    m = re.search(r"\b(?:с|строение)\s*([0-9А-ЯA-Zа-яa-z\-]+)\b", house_part, re.I | re.U)
    if m:
        building = m.group(1)

    return house, block, building


def _extract_street_type_and_name(street_part: str) -> tuple[str, str]:
    street_part = _collapse_spaces(street_part)
    street_type = ""
    street_name = street_part
    lowered = street_part.lower()

    for kind in _STREET_TYPES:
        if lowered.startswith(kind + " "):
            street_type = kind
            street_name = street_part[len(kind):].strip(" ,")
            return street_type, street_name
        if lowered.endswith(" " + kind):
            street_type = kind
            street_name = street_part[: -len(kind)].strip(" ,")
            return street_type, street_name
    return street_type, street_name


@lru_cache(maxsize=50000)
def normalize_building_address(address: str, city: str = "Москва") -> NormalizedBuildingAddress | None:
    if not address:
        return None

    cleaned = _RE_METRO_STRIP.sub("", address).strip(" ,")
    cleaned = _expand_abbr(cleaned)
    cleaned = _collapse_spaces(cleaned)
    if len(cleaned) < 5:
        return None

    normalized = _normalize_city_prefix(cleaned, city)
    normalized = _collapse_spaces(normalized)

    parts = [p.strip(" ,") for p in normalized.split(",") if p.strip(" ,")]
    if len(parts) < 2:
        return None

    city_part = parts[0]
    house_part = parts[-1]
    street_part = ", ".join(parts[1:-1]).strip(" ,") if len(parts) >= 3 else ""
    if not street_part:
        street_part = parts[1] if len(parts) == 2 else ""
    if not street_part or not house_part:
        return None

    street_type, street_name = _extract_street_type_and_name(street_part)
    house, block, building = _split_house_tokens(house_part)

    normalized_address = f"{city_part}, {street_part}, {house_part}".lower()
    normalized_address = _collapse_spaces(normalized_address)
    building_key = hashlib.sha1(normalized_address.encode("utf-8")).hexdigest()

    return NormalizedBuildingAddress(
        building_key=building_key,
        normalized_address=normalized_address,
        city=city_part,
        street_name=street_name.lower(),
        street_type=street_type.lower(),
        house=house.lower(),
        block=block.lower(),
        building=building.lower(),
    )


def lookup_cached_geo(address: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    normalized = normalize_building_address(address)
    if not normalized:
        return None

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        row = conn.execute(
            """
            SELECT building_key, normalized_address, city, street_name, street_type,
                   house, block, building, lat, lng, geo_source, geo_confidence,
                   source_address_raw, first_seen_at, last_seen_at, use_count
            FROM building_geo
            WHERE building_key = ?
            """,
            (normalized.building_key,),
        ).fetchone()
        return row
    finally:
        if own_conn:
            conn.close()


def upsert_building_geo(
    *,
    address: str,
    lat: float,
    lng: float,
    geo_source: str,
    geo_confidence: str,
    source_address_raw: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    normalized = normalize_building_address(address)
    if not normalized or not lat or not lng:
        return False

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        now = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO building_geo (
                building_key, normalized_address, city, street_name, street_type,
                house, block, building, lat, lng, geo_source, geo_confidence,
                source_address_raw, first_seen_at, last_seen_at, use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(building_key) DO UPDATE SET
                lat = excluded.lat,
                lng = excluded.lng,
                geo_source = excluded.geo_source,
                geo_confidence = excluded.geo_confidence,
                source_address_raw = COALESCE(excluded.source_address_raw, building_geo.source_address_raw),
                last_seen_at = excluded.last_seen_at,
                use_count = building_geo.use_count + 1
            """,
            (
                normalized.building_key,
                normalized.normalized_address,
                normalized.city,
                normalized.street_name,
                normalized.street_type,
                normalized.house,
                normalized.block,
                normalized.building,
                float(lat),
                float(lng),
                geo_source,
                geo_confidence,
                source_address_raw or address,
                now,
                now,
            ),
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def db_path() -> Path:
    return _DB_PATH
