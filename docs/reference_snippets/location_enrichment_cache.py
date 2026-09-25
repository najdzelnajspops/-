from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data" / "cache" / "location_enrichment_cache.db"

_DDL = """
CREATE TABLE IF NOT EXISTS geocode_cache (
    query_key            TEXT PRIMARY KEY,
    normalized_query     TEXT NOT NULL,
    provider_chain_hash  TEXT NOT NULL,
    provider_name        TEXT NOT NULL,
    lat                  REAL NOT NULL,
    lng                  REAL NOT NULL,
    precision            TEXT,
    confidence           TEXT,
    payload_json         TEXT,
    resolved_at          TEXT NOT NULL,
    last_seen_at         TEXT NOT NULL,
    use_count            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_geocode_cache_query
    ON geocode_cache (normalized_query);

CREATE TABLE IF NOT EXISTS places_cache (
    cache_key            TEXT PRIMARY KEY,
    lat_round            REAL NOT NULL,
    lng_round            REAL NOT NULL,
    radius_m             INTEGER NOT NULL,
    providers_hash       TEXT NOT NULL,
    schema_version       TEXT NOT NULL,
    result_json          TEXT NOT NULL,
    collected_at         TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    use_count            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_places_cache_coords
    ON places_cache (lat_round, lng_round, radius_m);

CREATE TABLE IF NOT EXISTS score_cache (
    cache_key            TEXT PRIMARY KEY,
    lat_round            REAL NOT NULL,
    lng_round            REAL NOT NULL,
    radius_m             INTEGER NOT NULL,
    providers_hash       TEXT NOT NULL,
    scoring_version      TEXT NOT NULL,
    result_json          TEXT NOT NULL,
    collected_at         TEXT NOT NULL,
    expires_at           TEXT NOT NULL,
    use_count            INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_score_cache_coords
    ON score_cache (lat_round, lng_round, radius_m);

CREATE TABLE IF NOT EXISTS provider_usage_log (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_name        TEXT NOT NULL,
    endpoint_name        TEXT NOT NULL,
    units                INTEGER NOT NULL DEFAULT 1,
    status               TEXT NOT NULL DEFAULT 'ok',
    run_tag              TEXT,
    meta_json            TEXT,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_provider_usage_log_provider_time
    ON provider_usage_log (provider_name, created_at);
"""


def open_db() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript(_DDL)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _collapse_spaces(text: str | None) -> str:
    return " ".join(str(text or "").strip().split())


def normalize_lookup_text(text: str | None) -> str:
    return _collapse_spaces(text).casefold()


def round_coord(value: float, digits: int = 5) -> float:
    return round(float(value), digits)


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def make_geocode_query_key(
    query: str,
    *,
    provider_chain_hash: str = "default",
    query_version: str = "v1",
) -> str:
    normalized = normalize_lookup_text(query)
    return _sha1(f"geocode:{query_version}:{provider_chain_hash}:{normalized}")


def make_places_cache_key(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    providers_hash: str = "default",
    schema_version: str = "v1",
) -> str:
    lat_r = round_coord(lat)
    lng_r = round_coord(lng)
    return _sha1(f"places:{schema_version}:{lat_r}:{lng_r}:{int(radius_m)}:{providers_hash}")


def make_score_cache_key(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    providers_hash: str = "default",
    scoring_version: str = "v1",
) -> str:
    lat_r = round_coord(lat)
    lng_r = round_coord(lng)
    return _sha1(f"score:{scoring_version}:{lat_r}:{lng_r}:{int(radius_m)}:{providers_hash}")


def lookup_geocode(
    query: str,
    *,
    provider_chain_hash: str = "default",
    query_version: str = "v1",
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    query_key = make_geocode_query_key(
        query,
        provider_chain_hash=provider_chain_hash,
        query_version=query_version,
    )
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        row = conn.execute(
            "SELECT * FROM geocode_cache WHERE query_key = ?",
            (query_key,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE geocode_cache SET last_seen_at = ?, use_count = use_count + 1 WHERE query_key = ?",
                (_utc_now_iso(), query_key),
            )
            if own_conn:
                conn.commit()
            row = conn.execute("SELECT * FROM geocode_cache WHERE query_key = ?", (query_key,)).fetchone()
        return row
    finally:
        if own_conn:
            conn.close()


def upsert_geocode(
    *,
    query: str,
    provider_name: str,
    lat: float,
    lng: float,
    precision: str = "",
    confidence: str = "",
    payload: dict | list | None = None,
    provider_chain_hash: str = "default",
    query_version: str = "v1",
    conn: sqlite3.Connection | None = None,
) -> bool:
    normalized = normalize_lookup_text(query)
    if not normalized:
        return False
    query_key = make_geocode_query_key(
        normalized,
        provider_chain_hash=provider_chain_hash,
        query_version=query_version,
    )
    now_iso = _utc_now_iso()
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        conn.execute(
            """
            INSERT INTO geocode_cache (
                query_key, normalized_query, provider_chain_hash, provider_name,
                lat, lng, precision, confidence, payload_json, resolved_at, last_seen_at, use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(query_key) DO UPDATE SET
                provider_name = excluded.provider_name,
                lat = excluded.lat,
                lng = excluded.lng,
                precision = excluded.precision,
                confidence = excluded.confidence,
                payload_json = excluded.payload_json,
                last_seen_at = excluded.last_seen_at,
                use_count = geocode_cache.use_count + 1
            """,
            (
                query_key,
                normalized,
                provider_chain_hash,
                provider_name,
                float(lat),
                float(lng),
                precision,
                confidence,
                payload_json,
                now_iso,
                now_iso,
            ),
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def _lookup_expiring_row(table: str, cache_key: str, *, conn: sqlite3.Connection) -> sqlite3.Row | None:
    row = conn.execute(f"SELECT * FROM {table} WHERE cache_key = ?", (cache_key,)).fetchone()
    if not row:
        return None
    expires_at = row["expires_at"]
    if expires_at and datetime.fromisoformat(expires_at) <= _utc_now():
        return None
    conn.execute(
        f"UPDATE {table} SET use_count = use_count + 1 WHERE cache_key = ?",
        (cache_key,),
    )
    return conn.execute(f"SELECT * FROM {table} WHERE cache_key = ?", (cache_key,)).fetchone()


def lookup_places(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    providers_hash: str = "default",
    schema_version: str = "v1",
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    cache_key = make_places_cache_key(
        lat, lng, radius_m, providers_hash=providers_hash, schema_version=schema_version
    )
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        row = _lookup_expiring_row("places_cache", cache_key, conn=conn)
        if own_conn:
            conn.commit()
        return row
    finally:
        if own_conn:
            conn.close()


def lookup_places_payload(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    providers_hash: str = "default",
    schema_version: str = "v1",
    conn: sqlite3.Connection | None = None,
) -> dict:
    row = lookup_places(
        lat,
        lng,
        radius_m,
        providers_hash=providers_hash,
        schema_version=schema_version,
        conn=conn,
    )
    if not row:
        return {}
    try:
        payload = json.loads(row["result_json"])
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def upsert_places(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    result: dict | list,
    providers_hash: str = "default",
    schema_version: str = "v1",
    ttl_days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> bool:
    cache_key = make_places_cache_key(
        lat, lng, radius_m, providers_hash=providers_hash, schema_version=schema_version
    )
    now = _utc_now()
    expires = now + timedelta(days=ttl_days)
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        conn.execute(
            """
            INSERT INTO places_cache (
                cache_key, lat_round, lng_round, radius_m, providers_hash, schema_version,
                result_json, collected_at, expires_at, use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json = excluded.result_json,
                collected_at = excluded.collected_at,
                expires_at = excluded.expires_at,
                use_count = places_cache.use_count + 1
            """,
            (
                cache_key,
                round_coord(lat),
                round_coord(lng),
                int(radius_m),
                providers_hash,
                schema_version,
                json.dumps(result, ensure_ascii=False),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def lookup_score(
    lat: float,
    lng: float,
    radius_m: int,
    *,
    providers_hash: str = "default",
    scoring_version: str = "v1",
    conn: sqlite3.Connection | None = None,
) -> sqlite3.Row | None:
    cache_key = make_score_cache_key(
        lat, lng, radius_m, providers_hash=providers_hash, scoring_version=scoring_version
    )
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        row = _lookup_expiring_row("score_cache", cache_key, conn=conn)
        if own_conn:
            conn.commit()
        return row
    finally:
        if own_conn:
            conn.close()


def upsert_score(
    *,
    lat: float,
    lng: float,
    radius_m: int,
    result: dict | list,
    providers_hash: str = "default",
    scoring_version: str = "v1",
    ttl_days: int = 30,
    conn: sqlite3.Connection | None = None,
) -> bool:
    cache_key = make_score_cache_key(
        lat, lng, radius_m, providers_hash=providers_hash, scoring_version=scoring_version
    )
    now = _utc_now()
    expires = now + timedelta(days=ttl_days)
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        conn.execute(
            """
            INSERT INTO score_cache (
                cache_key, lat_round, lng_round, radius_m, providers_hash, scoring_version,
                result_json, collected_at, expires_at, use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json = excluded.result_json,
                collected_at = excluded.collected_at,
                expires_at = excluded.expires_at,
                use_count = score_cache.use_count + 1
            """,
            (
                cache_key,
                round_coord(lat),
                round_coord(lng),
                int(radius_m),
                providers_hash,
                scoring_version,
                json.dumps(result, ensure_ascii=False),
                now.isoformat(),
                expires.isoformat(),
            ),
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def record_provider_usage(
    *,
    provider_name: str,
    endpoint_name: str,
    units: int = 1,
    status: str = "ok",
    run_tag: str = "",
    meta: dict | list | None = None,
    conn: sqlite3.Connection | None = None,
) -> bool:
    provider_name = str(provider_name or "").strip().lower()
    endpoint_name = str(endpoint_name or "").strip().lower()
    units = int(units or 0)
    if not provider_name or not endpoint_name or units <= 0:
        return False
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        conn.execute(
            """
            INSERT INTO provider_usage_log (
                provider_name, endpoint_name, units, status, run_tag, meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider_name,
                endpoint_name,
                units,
                str(status or "ok"),
                str(run_tag or ""),
                json.dumps(meta, ensure_ascii=False) if meta is not None else None,
                _utc_now_iso(),
            ),
        )
        if own_conn:
            conn.commit()
        return True
    finally:
        if own_conn:
            conn.close()


def get_provider_usage_stats(
    *,
    days: int = 1,
    conn: sqlite3.Connection | None = None,
) -> dict:
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        since = (_utc_now() - timedelta(days=max(1, int(days)))).isoformat()
        rows = conn.execute(
            """
            SELECT provider_name, endpoint_name, status, SUM(units) AS units
            FROM provider_usage_log
            WHERE created_at >= ?
            GROUP BY provider_name, endpoint_name, status
            ORDER BY provider_name, endpoint_name, status
            """,
            (since,),
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            provider_name = str(row["provider_name"])
            endpoint_name = str(row["endpoint_name"])
            status = str(row["status"])
            units = int(row["units"] or 0)
            provider_bucket = result.setdefault(provider_name, {"total_units": 0, "endpoints": {}})
            provider_bucket["total_units"] += units
            endpoint_bucket = provider_bucket["endpoints"].setdefault(endpoint_name, {"total_units": 0, "statuses": {}})
            endpoint_bucket["total_units"] += units
            endpoint_bucket["statuses"][status] = units
        return result
    finally:
        if own_conn:
            conn.close()


def get_stats(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    own_conn = conn is None
    if own_conn:
        conn = open_db()
    try:
        return {
            "geocode_rows": int(conn.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0]),
            "places_rows": int(conn.execute("SELECT COUNT(*) FROM places_cache").fetchone()[0]),
            "score_rows": int(conn.execute("SELECT COUNT(*) FROM score_cache").fetchone()[0]),
            "provider_usage_rows": int(conn.execute("SELECT COUNT(*) FROM provider_usage_log").fetchone()[0]),
        }
    finally:
        if own_conn:
            conn.close()
