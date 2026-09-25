from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from utils import building_geo_cache

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "cache" / "cadastral_registry_cache.db"

_DDL = """
CREATE TABLE IF NOT EXISTS cadastral_registry (
    cadastre_number     TEXT NOT NULL,
    cadastre_kind       TEXT NOT NULL,
    unom                INTEGER,
    normalized_address  TEXT NOT NULL,
    address_raw         TEXT NOT NULL,
    simple_address      TEXT,
    obj_type            TEXT,
    adm_area            TEXT,
    district            TEXT,
    lat                 REAL,
    lng                 REAL,
    source_dataset      TEXT NOT NULL,
    source_release      TEXT,
    source_record_json   TEXT,
    first_seen_at       TEXT NOT NULL,
    last_seen_at        TEXT NOT NULL,
    use_count           INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (cadastre_number, cadastre_kind)
);
CREATE INDEX IF NOT EXISTS idx_cadastral_registry_normalized_address
    ON cadastral_registry (normalized_address);
CREATE INDEX IF NOT EXISTS idx_cadastral_registry_unom
    ON cadastral_registry (unom);
CREATE INDEX IF NOT EXISTS idx_cadastral_registry_district
    ON cadastral_registry (district);

CREATE TABLE IF NOT EXISTS cadastral_values (
    cadastre_number       TEXT PRIMARY KEY,
    cadastral_value_rub   REAL NOT NULL,
    cadastral_value_per_m2 REAL,
    value_source          TEXT NOT NULL,
    value_source_ref      TEXT,
    source_updated_at     TEXT,
    payload_json          TEXT,
    first_seen_at         TEXT NOT NULL,
    last_seen_at          TEXT NOT NULL,
    use_count             INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_cadastral_values_updated
    ON cadastral_values (source_updated_at);
"""

_CAD_NUM_RE = re.compile(r"^\d{2}:\d{2}:\d{6,7}:\d+$")
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
_HOUSE_SUFFIX_RE = re.compile(r"^(?:с|стр|строение|к|корпус)\s*[0-9а-яa-z/-]+$", re.I | re.U)
_HOUSE_TOKEN_RE = r"[0-9а-яa-z/-]+"
_AVITO_TRANSIT_TAIL_RE = re.compile(
    r"[А-ЯЁ][А-ЯЁа-яёA-Za-z0-9 .'\-]+,\s*(?:(?:до|от)\s+)?\d+[–—\-]?\d*\s*мин\.?\s*$",
    re.U,
)
_AVITO_GLUE_AFTER_HOUSE_RE = re.compile(
    r"(\d+[а-яА-ЯёЁa-zA-Z]?(?:(?:к|с)\d+[а-яА-ЯёЁa-zA-Z]?)?)(?=[А-ЯЁ][а-яё]{2,})",
    re.U,
)
_AVITO_GLUE_BEFORE_LOCALITY_PREFIX_RE = re.compile(
    r"(\d+[а-яА-ЯёЁa-zA-Z]?(?:(?:к|с)\d+[а-яА-ЯёЁa-zA-Z]?)?)(?=(?:г|рп|пгт|пос|д|с)\.)",
    re.I | re.U,
)
_ADDRESS_TAIL_CITY_RE = re.compile(r"^[А-ЯЁ][А-ЯЁа-яёA-Za-z .'\-]+$", re.U)
_AVITO_MINUTES_TAIL_RE = re.compile(r"^(?:(?:до|от)\s+)?\d+[–—\-]?\d*\s*мин\.?$", re.I | re.U)
_MO_LEADING_SCOPE_RE = re.compile(r"^(?:московская область,\s*)+", re.I | re.U)
_MO_STREET_MARKER_RE = re.compile(
    r"\b(?:ул\.?|улица|пр-?т|проспект|пр-д|пр\.?|проезд|ш\.?|шоссе|б-р|бульвар|пер\.?|переулок|наб\.?|набережная|пл\.?|площадь|аллея|тупик|линия|дорога|тракт)\b",
    re.I | re.U,
)
_MO_HOUSE_READY_RE = re.compile(
    r"^\d+[0-9а-яa-z/-]*(?:\s*(?:к|корпус|с|стр|строение)\s*[0-9а-яa-z/-]+)*$",
    re.I | re.U,
)
_MO_COMPACT_HOUSE_TOKEN_RE = re.compile(r"^(\d+[а-яa-z]?)(к|с|стр)(\d+)$", re.I | re.U)
_MO_TOKENS = (
    "московская область",
    "балашиха",
    "видное",
    "волоколамск",
    "воскресенск",
    "дзержинский",
    "дмитров",
    "долгопрудный",
    "домодедово",
    "железнодорожный",
    "жуковский",
    "звенигород",
    "ивантеевка",
    "истра",
    "кашира",
    "клин",
    "коломна",
    "королев",
    "котельники",
    "красногорск",
    "лобня",
    "люберцы",
    "мытищи",
    "ногинск",
    "одинцово",
    "подольск",
    "протвино",
    "пушкино",
    "раменское",
    "реутов",
    "сергиев посад",
    "серпухов",
    "солнечногорск",
    "сходня",
    "томилино",
    "фрязино",
    "химки",
    "щелково",
    "электросталь",
)
_MO_TOKEN_PATTERNS = tuple(
    re.compile(rf"(?<![а-яa-z]){re.escape(token)}(?![а-яa-z])", re.I | re.U)
    for token in _MO_TOKENS
)


def open_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript(_DDL)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_cadastral_number(value: object) -> str:
    text = str(value or "").strip()
    text = text.translate(str.maketrans({"О": "0", "о": "0", "O": "0", "o": "0"}))
    text = re.sub(r"\s+", "", text, flags=re.U)
    return text if _CAD_NUM_RE.match(text) else ""


def _collapse_spaces(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or ""), flags=re.U).strip(" ,")


def _expand_abbr(address: str) -> str:
    for pat, full in _ADDR_ABBR:
        address = pat.sub(full, address)
    return address


def _normalize_scope_text(text: object) -> str:
    return _collapse_spaces(text).lower().replace("ё", "е")


def _cleanup_avito_address(address: str) -> str:
    cleaned = _collapse_spaces(address)
    if not cleaned:
        return ""
    cleaned = _AVITO_GLUE_BEFORE_LOCALITY_PREFIX_RE.sub(r"\1, ", cleaned)
    cleaned = _AVITO_GLUE_AFTER_HOUSE_RE.sub(r"\1, ", cleaned)
    cleaned = _AVITO_TRANSIT_TAIL_RE.sub("", cleaned).strip(" ,")
    parts = [part.strip(" ,") for part in cleaned.split(",") if part.strip(" ,")]
    if parts and _AVITO_MINUTES_TAIL_RE.match(parts[-1]):
        parts = parts[:-1]
        if parts and not re.search(r"\d", parts[-1], re.I | re.U):
            parts = parts[:-1]
    if len(parts) >= 2:
        tail = parts[-1]
        prev = parts[-2]
        if _ADDRESS_TAIL_CITY_RE.match(tail) and re.search(r"\d", prev, re.I | re.U):
            parts = parts[:-1]
    cleaned = ", ".join(parts)
    if _normalize_scope_text(cleaned) in {"москва", "российская федерация, город москва"}:
        return ""
    return _collapse_spaces(cleaned)


def _prepare_lookup_address(address: str, source: str | None = None) -> str:
    cleaned = _collapse_spaces(address)
    if not cleaned:
        return ""
    if str(source or "").strip().lower() == "avito":
        cleaned = _cleanup_avito_address(cleaned)
    return _collapse_spaces(cleaned)


def _prepare_scope_address(address: str, source: str | None = None) -> str:
    cleaned = _collapse_spaces(address)
    if not cleaned:
        return ""
    if str(source or "").strip().lower() == "avito":
        cleaned = _AVITO_GLUE_BEFORE_LOCALITY_PREFIX_RE.sub(r"\1, ", cleaned)
        cleaned = _AVITO_GLUE_AFTER_HOUSE_RE.sub(r"\1, ", cleaned)
        cleaned = re.sub(r",\s*(?:(?:до|от)\s+)?\d+[–—\-]?\d*\s*мин\.?\s*$", "", cleaned, flags=re.I | re.U)
    return _collapse_spaces(cleaned)


def detect_address_scope(address: str, source: str | None = None) -> str:
    cleaned = _normalize_scope_text(_prepare_scope_address(address, source=source))
    if not cleaned:
        return ""
    if "москва" in cleaned:
        return "moscow"
    if str(source or "").strip().lower() == "avito" and re.search(r"(?:^|, )(?:рп|пгт)\. ", cleaned, re.I | re.U):
        return "mo"
    if any(pattern.search(cleaned) for pattern in _MO_TOKEN_PATTERNS):
        return "mo"
    if str(source or "").strip().lower() == "avito" and re.search(r"\d+[–—\-]?\d*\s*мин\.?\s*$", cleaned, re.U):
        return "moscow"
    return "moscow"


def _extract_street_and_house_parts(address: str) -> tuple[str, str]:
    cleaned = _collapse_spaces(_expand_abbr(address))
    if not cleaned:
        return "", ""

    parts = [part.strip(" ,") for part in cleaned.split(",") if part.strip(" ,")]
    if len(parts) < 2:
        return "", ""

    house_parts = [parts[-1]]
    street_index = len(parts) - 2
    if len(parts) >= 3 and _HOUSE_SUFFIX_RE.match(parts[-1]) and re.search(r"\d", parts[-2], re.I | re.U):
        house_parts = [parts[-2], parts[-1]]
        street_index = len(parts) - 3

    if street_index < 0:
        return "", ""
    return parts[street_index], ", ".join(house_parts)


def _split_address_parts(address: object) -> list[str]:
    return [part.strip(" ,") for part in str(address or "").split(",") if part.strip(" ,")]


def _canonicalize_mo_street_part(street_part: str) -> str:
    street_part = _expand_abbr(str(street_part or ""))
    street_part = _collapse_spaces(street_part).lower().replace("ё", "е")
    for marker in (
        "улица",
        "проспект",
        "проезд",
        "шоссе",
        "бульвар",
        "переулок",
        "набережная",
        "площадь",
        "аллея",
        "тупик",
        "линия",
        "дорога",
        "тракт",
    ):
        if street_part.startswith(marker + " "):
            return street_part
        if street_part.endswith(" " + marker):
            name = street_part[: -len(marker)].strip(" ,")
            return f"{marker} {name}".strip()
    return street_part


def _canonicalize_mo_house_part(house_part: str) -> str:
    text = _collapse_spaces(house_part).lower().replace("ё", "е")
    if not text:
        return ""
    text = re.sub(r"\bстр\.\b", "стр", text, flags=re.I | re.U)
    match = _MO_COMPACT_HOUSE_TOKEN_RE.match(text)
    if match:
        return f"{match.group(1)} {match.group(2)}{match.group(3)}"
    return text


def canonicalize_mo_cadastral_address(address: str, source: str | None = None) -> str:
    prepared = _prepare_scope_address(address, source=source)
    cleaned = _MO_LEADING_SCOPE_RE.sub("", prepared).strip(" ,")
    parts = _split_address_parts(cleaned)
    if len(parts) < 3:
        return ""

    locality = _collapse_spaces(parts[0]).lower().replace("ё", "е")
    middle_parts = parts[1:-1]
    house_part = _canonicalize_mo_house_part(parts[-1])
    if not locality or not middle_parts or not house_part or not _MO_HOUSE_READY_RE.match(house_part):
        return ""

    street_part = ""
    for part in reversed(middle_parts):
        if _MO_STREET_MARKER_RE.search(part):
            street_part = part
            break
    if not street_part:
        street_part = middle_parts[-1]
    street_part = _canonicalize_mo_street_part(street_part)
    if not street_part or not _MO_STREET_MARKER_RE.search(street_part):
        return ""
    return "|".join((locality, street_part, house_part))


def _parse_house_part(house_part: str) -> tuple[str, str, str]:
    text = _collapse_spaces(house_part).lower().replace("ё", "е")
    if not text:
        return "", "", ""

    text = re.sub(r"^(?:дом|д\.?)\s*", "дом ", text, flags=re.I | re.U)
    text = re.sub(r"\bстр\.\b", "строение", text, flags=re.I | re.U)
    text = re.sub(r"\bстр\b", "строение", text, flags=re.I | re.U)

    base = ""
    block = ""
    building = ""
    segments = [_collapse_spaces(part).lower() for part in text.split(",") if _collapse_spaces(part)]
    if not segments:
        return "", "", ""

    first = segments[0]
    patterns = [
        (
            rf"^дом\s*({_HOUSE_TOKEN_RE})\s*(?:корпус|к)\s*({_HOUSE_TOKEN_RE})\s*(?:строение|с)\s*({_HOUSE_TOKEN_RE})$",
            "block_building",
        ),
        (
            rf"^({_HOUSE_TOKEN_RE})(?:корпус|к)({_HOUSE_TOKEN_RE})(?:строение|с)({_HOUSE_TOKEN_RE})$",
            "block_building_compact",
        ),
        (rf"^дом\s*({_HOUSE_TOKEN_RE})\s*(?:корпус|к)\s*({_HOUSE_TOKEN_RE})$", "block"),
        (rf"^дом\s*({_HOUSE_TOKEN_RE})\s*(?:строение|с)\s*({_HOUSE_TOKEN_RE})$", "building"),
        (rf"^({_HOUSE_TOKEN_RE})(?:корпус|к)({_HOUSE_TOKEN_RE})$", "block"),
        (rf"^({_HOUSE_TOKEN_RE})(?:строение|с)({_HOUSE_TOKEN_RE})$", "building"),
        (rf"^({_HOUSE_TOKEN_RE})(к)({_HOUSE_TOKEN_RE})$", "block_compact"),
        (rf"^({_HOUSE_TOKEN_RE})(с)({_HOUSE_TOKEN_RE})$", "building_compact"),
        (rf"^дом\s*({_HOUSE_TOKEN_RE})$", "base"),
        (rf"^({_HOUSE_TOKEN_RE})$", "base"),
    ]
    for pattern, kind in patterns:
        match = re.match(pattern, first, flags=re.I | re.U)
        if not match:
            continue
        if kind == "base":
            base = match.group(1)
        elif kind == "block_building":
            base = match.group(1)
            block = match.group(2)
            building = match.group(3)
        elif kind == "block_building_compact":
            base = match.group(1)
            block = match.group(2)
            building = match.group(3)
        elif kind in {"block", "building"}:
            base = match.group(1)
            if kind == "block":
                block = match.group(2)
            else:
                building = match.group(2)
        elif kind in {"block_compact", "building_compact"}:
            base = match.group(1)
            if kind == "block_compact":
                block = match.group(3)
            else:
                building = match.group(3)
        break

    for segment in segments[1:]:
        match = re.match(rf"^(?:корпус|к)\s*({_HOUSE_TOKEN_RE})$", segment, flags=re.I | re.U)
        if match:
            block = match.group(1)
            continue
        match = re.match(rf"^(?:строение|с)\s*({_HOUSE_TOKEN_RE})$", segment, flags=re.I | re.U)
        if match:
            building = match.group(1)

    return base, block, building


def _compact_house_token(base: str, block: str, building: str) -> str:
    base_value = _collapse_spaces(str(base or "")).lower().replace("ё", "е")
    block_value = _collapse_spaces(str(block or "")).lower().replace("ё", "е")
    building_value = _collapse_spaces(str(building or "")).lower().replace("ё", "е")
    if not base_value:
        return ""
    token = base_value
    if block_value:
        token += f"к{block_value}"
    if building_value:
        token += f"с{building_value}"
    return token


def _normalized_address_house_token(normalized_address: str) -> str:
    _, house_part = _extract_street_and_house_parts(normalized_address)
    if not house_part:
        return ""
    base, block, building = _parse_house_part(house_part)
    return _compact_house_token(base, block, building)


def _extract_title_house_token(title: object, address: object) -> str:
    title_text = str(title or "").strip()
    address_text = str(address or "").strip()
    if not title_text or not address_text:
        return ""
    normalized = building_geo_cache.normalize_building_address(address_text)
    if not normalized:
        return ""
    address_house = re.sub(r"\s+", "", str(normalized.house or "").lower())
    if not address_house:
        return ""
    title_l = title_text.lower()
    street_phrase = f"{normalized.street_type} {normalized.street_name}".strip().lower()
    if not street_phrase:
        return ""
    street_index = title_l.find(street_phrase)
    if street_index < 0:
        return ""
    tail = title_l[street_index + len(street_phrase) :]
    match = re.search(
        r"^\s*,?\s*(?:д\.?\s*)?(?P<house>\d+(?:(?:ас|корпус|строение|к|с)\s*\d+[а-яa-z]?|[а-яa-z])?)",
        tail,
        flags=re.I | re.U,
    )
    if not match:
        return ""
    title_house = re.sub(r"\s+", "", str(match.group("house") or "").lower())
    if not title_house or title_house == address_house:
        return ""
    if not title_house.startswith(address_house):
        return ""
    return title_house


def _title_conflicts_with_variant_selection(
    selected_rows: list[sqlite3.Row],
    *,
    normalized_query: str,
    title: object = None,
    address: object = None,
) -> bool:
    if not selected_rows:
        return False
    title_token = _extract_title_house_token(title, address)
    if not title_token:
        return False
    query_token = _normalized_address_house_token(normalized_query)
    candidate_token = _normalized_address_house_token(_row_text(selected_rows[0], "normalized_address"))
    if not query_token or not candidate_token:
        return False
    if candidate_token == query_token:
        return False
    return candidate_token != title_token


def canonicalize_cadastral_address(address: str, source: str | None = None) -> str:
    prepared = _prepare_lookup_address(address, source=source)
    if detect_address_scope(prepared, source=source) != "moscow":
        return ""

    street_part, house_part = _extract_street_and_house_parts(prepared)
    if not street_part or not house_part:
        return ""

    base, block, building = _parse_house_part(house_part)
    if not base:
        return ""

    canonical_house = f"дом {base}"
    if block:
        canonical_house += f", корпус {block}"
    if building:
        canonical_house += f", строение {building}"

    normalized = building_geo_cache.normalize_building_address(
        f"Москва, {street_part}, {canonical_house}"
    )
    return normalized.normalized_address if normalized is not None else ""


def _row_text(row: sqlite3.Row | dict, key: str) -> str:
    try:
        return str(row[key] or "").strip()
    except Exception:
        return ""


def _row_float(row: sqlite3.Row | dict, key: str) -> float | None:
    try:
        value = row[key]
    except Exception:
        return None
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _row_kind_priority(row: sqlite3.Row | dict) -> int:
    return 1 if _row_text(row, "cadastre_kind").lower() == "building" else 0


def _query_registry_rows_by_normalized(conn: sqlite3.Connection, normalized_address: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT r.*, v.cadastral_value_rub, v.cadastral_value_per_m2,
               v.value_source, v.value_source_ref, v.source_updated_at
        FROM cadastral_registry r
        LEFT JOIN cadastral_values v
          ON v.cadastre_number = r.cadastre_number
        WHERE r.normalized_address = ?
        ORDER BY CASE r.cadastre_kind WHEN 'building' THEN 0 ELSE 1 END,
                 CASE WHEN v.cadastral_value_rub IS NULL THEN 1 ELSE 0 END,
                 r.cadastre_number
        """,
        (normalized_address,),
    ).fetchall()
    return list(rows or [])


def _select_unique_registry_variant(rows: list[sqlite3.Row], *, allow_land: bool = False) -> list[sqlite3.Row]:
    if not rows:
        return []
    building_rows = [row for row in rows if _row_text(row, "cadastre_kind").lower() == "building"]
    if len(building_rows) == 1:
        normalized = _row_text(building_rows[0], "normalized_address")
        return [row for row in rows if _row_text(row, "normalized_address") == normalized]
    if allow_land:
        land_rows = [row for row in rows if _row_text(row, "cadastre_kind").lower() == "land"]
        if len(land_rows) == 1:
            normalized = _row_text(land_rows[0], "normalized_address")
            return [row for row in rows if _row_text(row, "normalized_address") == normalized]
    return []


def _select_geo_nearest_registry_variant(
    rows: list[sqlite3.Row], *, lat: object = None, lng: object = None
) -> list[sqlite3.Row]:
    lat_value = _coerce_positive_float(lat)
    lng_value = _coerce_positive_float(lng)
    if lat_value is None or lng_value is None:
        return []

    candidates: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        if _row_text(row, "cadastre_kind").lower() != "building":
            continue
        row_lat = _row_float(row, "lat")
        row_lng = _row_float(row, "lng")
        if row_lat is None or row_lng is None:
            continue
        distance = ((row_lat - lat_value) ** 2) + ((row_lng - lng_value) ** 2)
        candidates.append((distance, row))

    if len(candidates) < 2:
        return []

    candidates.sort(key=lambda item: (item[0], _row_text(item[1], "cadastre_number")))
    best_distance, best_row = candidates[0]
    second_distance, _ = candidates[1]
    if best_distance > 2.5e-7:
        return []
    if second_distance <= 0.0:
        return []
    if second_distance < (best_distance * 4.0) and (second_distance - best_distance) < 2.5e-7:
        return []

    normalized = _row_text(best_row, "normalized_address")
    return [row for row in rows if _row_text(row, "normalized_address") == normalized]


def _iter_moscow_normalized_address_variants(normalized: str) -> list[tuple[str, bool]]:
    text = _collapse_spaces(normalized).lower().replace("ё", "е")
    if not text.startswith("москва, "):
        return []
    variants: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def add(value: str, *, allow_land: bool = False) -> None:
        key = _collapse_spaces(value).lower().replace("ё", "е")
        if not key or key == text or key in seen:
            return
        seen.add(key)
        variants.append((key, allow_land))

    if ", строение " in text:
        add(re.sub(r",\s*строение\s*[^,]+$", "", text, flags=re.I | re.U), allow_land=False)

    match = re.match(r"^(москва,\s*.+?,\s*)дом\s*([0-9а-я/-]+)$", text, flags=re.I | re.U)
    if match:
        add(match.group(1) + f"земельный участок {match.group(2)}", allow_land=True)

    return variants


def _should_skip_broad_moscow_like_fallback(normalized: str, *, source: str | None = None) -> bool:
    text = _collapse_spaces(normalized).lower().replace("ё", "е")
    if not text.startswith("москва, "):
        return False
    if (source or "").strip().lower() != "cian":
        return False
    return ", дом " in text and ", строение " in text


def _select_parent_registry(rows: list[sqlite3.Row]) -> tuple[sqlite3.Row | None, str]:
    if not rows:
        return None, ""

    ranked = sorted(
        rows,
        key=lambda row: (
            _row_kind_priority(row),
            1 if _row_float(row, "cadastral_value_per_m2") not in (None, 0.0) else 0,
            1 if _row_float(row, "cadastral_value_rub") not in (None, 0.0) else 0,
            _row_text(row, "cadastre_number"),
        ),
        reverse=True,
    )
    parent = ranked[0]
    has_value_per_m2 = _row_float(parent, "cadastral_value_per_m2") not in (None, 0.0)
    has_value = _row_float(parent, "cadastral_value_rub") not in (None, 0.0)
    if _row_kind_priority(parent) and has_value_per_m2:
        return parent, "building_with_value_per_m2"
    if _row_kind_priority(parent) and has_value:
        return parent, "building_with_value"
    if _row_kind_priority(parent):
        return parent, "building_first"
    if has_value_per_m2:
        return parent, "value_per_m2_fallback"
    if has_value:
        return parent, "value_fallback"
    return parent, "first_match"


def _build_unresolved_context(*, scope: str, status: str) -> dict:
    return {
        "source": "cadastral_registry_cache",
        "cadastral_scope": scope,
        "cadastral_resolution_status": status,
        "registry_matches_count": 0,
        "registry_matches": [],
        "primary_registry": None,
        "parent_registry": None,
        "parent_selection_reason": "",
        "cadastral_numbers": [],
        "cadastral_value_rub": None,
        "cadastral_value_per_m2": None,
        "cadastral_value_mode": "",
        "cadastral_registry_value_rub": None,
        "cadastral_registry_value_per_m2": None,
        "derived_cadastral_value_rub": None,
        "cadastral_value_source": None,
        "cadastral_value_source_ref": None,
    }


def _coerce_positive_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except Exception:
        return None
    return number if number > 0 else None


def _finalize_context_with_area(context: dict, area_m2: object = None) -> dict:
    result = dict(context or {})
    registry_value_rub = _coerce_positive_float(result.get("cadastral_registry_value_rub"))
    registry_value_per_m2 = _coerce_positive_float(result.get("cadastral_registry_value_per_m2"))
    area_value = _coerce_positive_float(area_m2)
    derived_value_rub = None
    value_rub = registry_value_rub
    value_mode = "direct" if registry_value_rub is not None else ""
    if registry_value_per_m2 is not None and area_value is not None:
        derived_value_rub = round(registry_value_per_m2 * area_value, 2)
        value_rub = derived_value_rub
        value_mode = "derived"
    result["derived_cadastral_value_rub"] = derived_value_rub
    result["cadastral_value_rub"] = value_rub
    result["cadastral_value_per_m2"] = registry_value_per_m2
    result["cadastral_value_mode"] = value_mode
    status = str(result.get("cadastral_resolution_status") or "")
    if result.get("parent_registry"):
        result["cadastral_resolution_status"] = "cadastre_resolved" if value_rub is not None else "cadastre_registry_only"
    elif not status:
        result["cadastral_resolution_status"] = "cadastre_no_registry_match"
    return result


def apply_listing_area_to_context(context: dict, area_m2: object = None) -> dict:
    return _finalize_context_with_area(context, area_m2=area_m2)


def _coalesce_text(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _iter_cadastre_numbers(raw: dict) -> Iterable[tuple[str, str]]:
    for field, kind in (("KAD_N", "building"), ("KAD_ZU", "land")):
        items = raw.get(field) or []
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        for item in items:
            cad_num = ""
            if isinstance(item, dict):
                cad_num = normalize_cadastral_number(item.get(field))
                cad_num = cad_num or normalize_cadastral_number(
                    item.get("cad_num") or item.get("cadastre_number")
                )
            else:
                cad_num = normalize_cadastral_number(item)
            if cad_num:
                yield kind, cad_num


def _extract_center(raw: dict) -> tuple[float | None, float | None]:
    center = raw.get("geodata_center") or {}
    coords = center.get("coordinates") if isinstance(center, dict) else None
    if isinstance(coords, list) and len(coords) >= 2:
        try:
            lng = float(coords[0])
            lat = float(coords[1])
            return lat, lng
        except Exception:
            return None, None
    return None, None


def _build_registry_row(raw: dict, cadastre_kind: str, cadastre_number: str) -> dict:
    normalized_address = canonicalize_cadastral_address(
        str(raw.get("SIMPLE_ADDRESS") or raw.get("ADDRESS") or "")
    )
    if not normalized_address:
        return {}
    lat, lng = _extract_center(raw)
    source_record = {
        "UNOM": raw.get("UNOM"),
        "OBJ_TYPE": raw.get("OBJ_TYPE"),
        "ADDRESS": raw.get("ADDRESS"),
        "SIMPLE_ADDRESS": raw.get("SIMPLE_ADDRESS"),
        "ADM_AREA": raw.get("ADM_AREA"),
        "DISTRICT": raw.get("DISTRICT"),
        "L1_TYPE": raw.get("L1_TYPE"),
        "L1_VALUE": raw.get("L1_VALUE"),
        "L2_TYPE": raw.get("L2_TYPE"),
        "L2_VALUE": raw.get("L2_VALUE"),
        "L3_TYPE": raw.get("L3_TYPE"),
        "L3_VALUE": raw.get("L3_VALUE"),
        "geo_center": {"lat": lat, "lng": lng},
    }
    return {
        "cadastre_number": cadastre_number,
        "cadastre_kind": cadastre_kind,
        "unom": int(raw["UNOM"]) if raw.get("UNOM") not in (None, "") else None,
        "normalized_address": normalized_address,
        "address_raw": str(raw.get("ADDRESS") or ""),
        "simple_address": _coalesce_text(raw.get("SIMPLE_ADDRESS")),
        "obj_type": _coalesce_text(raw.get("OBJ_TYPE")),
        "adm_area": _coalesce_text(raw.get("ADM_AREA")),
        "district": _coalesce_text(raw.get("DISTRICT")),
        "lat": lat,
        "lng": lng,
        "source_dataset": "data.mos.ru/60562",
        "source_release": _coalesce_text(raw.get("DREG"), raw.get("D_FIAS")),
        "source_record_json": json.dumps(source_record, ensure_ascii=False),
    }


def normalize_cadastre_kind(value: object) -> str:
    text = _collapse_spaces(value).lower().replace("ё", "е")
    if not text:
        return ""
    if text in {"building", "строение", "здание", "house", "parent"}:
        return "building"
    if text in {"premise", "помещение", "room"}:
        return "premise"
    if text in {"land", "земля", "земельный участок", "zu", "parcel"}:
        return "land"
    return text


def _prepare_registry_entry(row: dict) -> dict:
    cadastre_number = normalize_cadastral_number(row.get("cadastre_number"))
    cadastre_kind = normalize_cadastre_kind(row.get("cadastre_kind"))
    normalized_address = _collapse_spaces(row.get("normalized_address")).lower().replace("ё", "е")
    if not cadastre_number or not cadastre_kind or not normalized_address:
        return {}
    payload = row.get("source_record_json")
    if payload not in (None, "") and not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return {
        "cadastre_number": cadastre_number,
        "cadastre_kind": cadastre_kind,
        "unom": int(row["unom"]) if row.get("unom") not in (None, "") else None,
        "normalized_address": normalized_address,
        "address_raw": str(row.get("address_raw") or ""),
        "simple_address": _coalesce_text(row.get("simple_address")),
        "obj_type": _coalesce_text(row.get("obj_type")),
        "adm_area": _coalesce_text(row.get("adm_area")),
        "district": _coalesce_text(row.get("district")),
        "lat": _row_float(row, "lat"),
        "lng": _row_float(row, "lng"),
        "source_dataset": str(row.get("source_dataset") or ""),
        "source_release": _coalesce_text(row.get("source_release")),
        "source_record_json": str(payload or ""),
    }


def _upsert_registry_rows(rows: list[dict], conn: sqlite3.Connection | None = None) -> int:
    if not rows:
        return 0

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        now = _utc_now_iso()
        for row in rows:
            conn.execute(
                """
                INSERT INTO cadastral_registry (
                    cadastre_number, cadastre_kind, unom, normalized_address,
                    address_raw, simple_address, obj_type, adm_area, district,
                    lat, lng, source_dataset, source_release, source_record_json,
                    first_seen_at, last_seen_at, use_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(cadastre_number, cadastre_kind) DO UPDATE SET
                    unom = excluded.unom,
                    normalized_address = excluded.normalized_address,
                    address_raw = excluded.address_raw,
                    simple_address = excluded.simple_address,
                    obj_type = excluded.obj_type,
                    adm_area = excluded.adm_area,
                    district = excluded.district,
                    lat = excluded.lat,
                    lng = excluded.lng,
                    source_dataset = excluded.source_dataset,
                    source_release = excluded.source_release,
                    source_record_json = excluded.source_record_json,
                    last_seen_at = excluded.last_seen_at,
                    use_count = cadastral_registry.use_count + 1
                """,
                (
                    row["cadastre_number"],
                    row["cadastre_kind"],
                    row["unom"],
                    row["normalized_address"],
                    row["address_raw"],
                    row["simple_address"],
                    row["obj_type"],
                    row["adm_area"],
                    row["district"],
                    row["lat"],
                    row["lng"],
                    row["source_dataset"],
                    row["source_release"],
                    row["source_record_json"],
                    now,
                    now,
                ),
            )
        if own_conn:
            conn.commit()
        return len(rows)
    finally:
        if own_conn:
            conn.close()


def upsert_registry_record(raw: dict, conn: sqlite3.Connection | None = None) -> int:
    rows: list[dict] = []
    for cadastre_kind, cadastre_number in _iter_cadastre_numbers(raw):
        row = _build_registry_row(raw, cadastre_kind, cadastre_number)
        if row:
            rows.append(row)

    if not rows:
        return 0
    return _upsert_registry_rows(rows, conn=conn)


def upsert_registry_entry(row: dict, conn: sqlite3.Connection | None = None) -> int:
    prepared = _prepare_registry_entry(row)
    if not prepared:
        return 0
    return _upsert_registry_rows([prepared], conn=conn)


def upsert_cadastral_value(
    cadastre_number: str,
    cadastral_value_rub: float,
    *,
    cadastral_value_per_m2: float | None = None,
    value_source: str = "pynspd",
    value_source_ref: str | None = None,
    source_updated_at: str | None = None,
    payload_json: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    cadastre_number = normalize_cadastral_number(cadastre_number)
    if not cadastre_number or cadastral_value_rub is None:
        return False

    try:
        value = float(cadastral_value_rub)
    except Exception:
        return False
    if value <= 0:
        return False

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        now = _utc_now_iso()
        conn.execute(
            """
            INSERT INTO cadastral_values (
                cadastre_number, cadastral_value_rub, cadastral_value_per_m2,
                value_source, value_source_ref, source_updated_at, payload_json,
                first_seen_at, last_seen_at, use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(cadastre_number) DO UPDATE SET
                cadastral_value_rub = excluded.cadastral_value_rub,
                cadastral_value_per_m2 = excluded.cadastral_value_per_m2,
                value_source = excluded.value_source,
                value_source_ref = excluded.value_source_ref,
                source_updated_at = excluded.source_updated_at,
                payload_json = excluded.payload_json,
                last_seen_at = excluded.last_seen_at,
                use_count = cadastral_values.use_count + 1
            """,
            (
                cadastre_number,
                value,
                float(cadastral_value_per_m2) if cadastral_value_per_m2 not in (None, "") else None,
                value_source,
                value_source_ref,
                source_updated_at,
                payload_json,
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


def lookup_registry_by_cadastral_number(
    cadastre_number: str, conn: sqlite3.Connection | None = None
) -> sqlite3.Row | None:
    cadastre_number = normalize_cadastral_number(cadastre_number)
    if not cadastre_number:
        return None

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        return conn.execute(
            """
            SELECT r.*, v.cadastral_value_rub, v.cadastral_value_per_m2,
                   v.value_source, v.value_source_ref, v.source_updated_at
            FROM cadastral_registry r
            LEFT JOIN cadastral_values v
              ON v.cadastre_number = r.cadastre_number
            WHERE r.cadastre_number = ?
            ORDER BY CASE r.cadastre_kind WHEN 'building' THEN 0 ELSE 1 END,
                     CASE WHEN v.cadastral_value_rub IS NULL THEN 1 ELSE 0 END
            LIMIT 1
            """,
            (cadastre_number,),
        ).fetchone()
    finally:
        if own_conn:
            conn.close()


def lookup_registry_by_address(
    address: str,
    conn: sqlite3.Connection | None = None,
    source: str | None = None,
    lat: object = None,
    lng: object = None,
    title: object = None,
) -> list[sqlite3.Row]:
    scope = detect_address_scope(address, source=source)
    if scope == "mo":
        normalized = canonicalize_mo_cadastral_address(address, source=source)
    else:
        normalized = canonicalize_cadastral_address(address, source=source)
    if not normalized:
        return []

    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        rows = _query_registry_rows_by_normalized(conn, normalized)
        if rows:
            return rows

        if scope != "moscow" or not normalized.startswith("москва, "):
            return []

        for variant_normalized, allow_land in _iter_moscow_normalized_address_variants(normalized):
            variant_rows = _query_registry_rows_by_normalized(conn, variant_normalized)
            selected = _select_unique_registry_variant(variant_rows, allow_land=allow_land)
            if selected:
                return selected

        if _should_skip_broad_moscow_like_fallback(normalized, source=source):
            return []

        tail = normalized.split(", ", 1)[1].strip()
        if not tail:
            return []

        variant_rows = conn.execute(
            """
            SELECT r.*, v.cadastral_value_rub, v.cadastral_value_per_m2,
                   v.value_source, v.value_source_ref, v.source_updated_at
            FROM cadastral_registry r
            LEFT JOIN cadastral_values v
              ON v.cadastre_number = r.cadastre_number
            WHERE r.normalized_address LIKE ?
            ORDER BY CASE r.cadastre_kind WHEN 'building' THEN 0 ELSE 1 END,
                     CASE WHEN v.cadastral_value_rub IS NULL THEN 1 ELSE 0 END,
                     r.cadastre_number
            """,
            (f"%{tail}%",),
        ).fetchall()
        variant_rows = list(variant_rows or [])
        if not variant_rows:
            return []
        selected = _select_unique_registry_variant(variant_rows, allow_land=False)
        if selected and not _title_conflicts_with_variant_selection(
            selected,
            normalized_query=normalized,
            title=title,
            address=address,
        ):
            return selected
        geo_selected = _select_geo_nearest_registry_variant(variant_rows, lat=lat, lng=lng)
        if _title_conflicts_with_variant_selection(
            geo_selected,
            normalized_query=normalized,
            title=title,
            address=address,
        ):
            return []
        return geo_selected
    finally:
        if own_conn:
            conn.close()


def lookup_cadastral_context(
    *,
    address: str | None = None,
    cadastre_number: str | None = None,
    area_m2: object = None,
    lat: object = None,
    lng: object = None,
    title: object = None,
    source: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    own_conn = conn is None
    if own_conn:
        conn = open_db()

    try:
        if cadastre_number:
            primary = lookup_registry_by_cadastral_number(cadastre_number, conn=conn)
            rows = [primary] if primary is not None else []
            scope = ""
        elif address:
            scope = detect_address_scope(address, source=source)
            rows = lookup_registry_by_address(
                address,
                conn=conn,
                source=source,
                lat=lat,
                lng=lng,
                title=title,
            )
            primary = rows[0] if rows else None
        else:
            rows = []
            primary = None
            scope = ""

        if not rows:
            if address:
                final_scope = scope or detect_address_scope(address, source=source)
                if final_scope == "mo":
                    return _build_unresolved_context(scope=final_scope, status="cadastre_unresolved_mo")
                return _build_unresolved_context(scope=final_scope, status="cadastre_no_registry_match")
            return _build_unresolved_context(scope=scope, status="cadastre_no_registry_match")

        cadastral_numbers = []
        parent, parent_selection_reason = _select_parent_registry(rows)
        registry_value_rub = None
        registry_value_per_m2 = None
        value_source = None
        value_source_ref = None
        for row in rows:
            cadastral_numbers.append(str(row["cadastre_number"]))
        if parent is not None:
            if parent["cadastral_value_rub"] not in (None, ""):
                registry_value_rub = float(parent["cadastral_value_rub"])
            if parent["cadastral_value_per_m2"] not in (None, ""):
                registry_value_per_m2 = float(parent["cadastral_value_per_m2"])
            if registry_value_rub is not None or registry_value_per_m2 is not None:
                value_source = str(parent["value_source"] or "")
                value_source_ref = str(parent["value_source_ref"] or "")

        context = {
            "source": "cadastral_registry_cache",
            "cadastral_scope": scope or "moscow",
            "cadastral_resolution_status": "cadastre_registry_only",
            "registry_matches_count": len(rows),
            "registry_matches": [dict(row) for row in rows],
            "primary_registry": dict(primary) if primary is not None else None,
            "parent_registry": dict(parent) if parent is not None else None,
            "parent_selection_reason": parent_selection_reason,
            "cadastral_numbers": cadastral_numbers,
            "cadastral_value_rub": None,
            "cadastral_value_per_m2": registry_value_per_m2,
            "cadastral_value_mode": "",
            "cadastral_registry_value_rub": registry_value_rub,
            "cadastral_registry_value_per_m2": registry_value_per_m2,
            "derived_cadastral_value_rub": None,
            "cadastral_value_source": value_source,
            "cadastral_value_source_ref": value_source_ref,
        }
        if not context["primary_registry"]:
            context["primary_registry"] = context["parent_registry"]
        return _finalize_context_with_area(context, area_m2=area_m2)
    finally:
        if own_conn:
            conn.close()
