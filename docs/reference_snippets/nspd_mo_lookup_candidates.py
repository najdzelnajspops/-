from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_SEED = ROOT / "data" / "reference" / "cadastral_mo_dataset_seed.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "reference" / "cadastral_mo_nspd_candidates.jsonl"
DEFAULT_REPORT = ROOT / "data" / "reference" / "cadastral_mo_nspd_candidates.report.json"
DEFAULT_CACHE_FOLDER = ROOT / "data" / "cache" / "nspd_cache"
DEFAULT_TOP_CANDIDATES = 12
DEFAULT_VARIANTS_LIMIT = 3

BUILDING_CATEGORIES = {"здания", "здание", "строения", "сооружения", "объекты незавершенного строительства"}
STREET_TYPE_TOKENS = {
    "улица",
    "ул",
    "переулок",
    "пер",
    "проезд",
    "пр",
    "шоссе",
    "ш",
    "бульвар",
    "бул",
    "проспект",
    "пркт",
    "аллея",
    "линия",
    "набережная",
    "наб",
    "площадь",
    "пл",
    "тракт",
    "тупик",
}
_TRANSPORT_ERROR_TYPES = {"connecterror", "connecttimeout", "readtimeout", "proxyerror", "remoteprotocolerror"}
_TRANSPORT_ERROR_TOKENS = (
    "connection reset",
    "remote host",
    "удаленный хост",
    "принудительно разорвал",
    "timed out",
    "timeout",
    "connection aborted",
    "remote end closed",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_transport_lookup_error(*, error_type: object, error_text: object) -> bool:
    error_type_key = _normalize_key(error_type)
    error_text_key = _normalize_key(error_text)
    if error_type_key in _TRANSPORT_ERROR_TYPES:
        return True
    return any(token in error_text_key for token in _TRANSPORT_ERROR_TOKENS)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").strip().replace("\xa0", " ").split())


def _normalize_key(value: object) -> str:
    return _normalize_text(value).lower().replace("ё", "е")


def _coerce_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _iter_seed_rows(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
        return
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                yield dict(row)
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload if isinstance(payload, list) else payload.get("rows") or []
    for row in rows:
        if isinstance(row, dict):
            yield row


def _seed_queries(row: dict, *, variants_limit: int) -> tuple[str, list[str], dict]:
    lookup = row.get("lookup_queries") or {}
    primary = _normalize_text(lookup.get("primary") or row.get("display_address"))
    variants: list[str] = []
    seen: set[str] = set()
    for candidate in [primary, *(lookup.get("variants") or []), *(row.get("address_variants") or [])]:
        text = _normalize_text(candidate)
        if not text:
            continue
        key = _normalize_key(text)
        if key in seen:
            continue
        seen.add(key)
        variants.append(text)
    geo_anchor = lookup.get("geo_anchor") or {}
    return primary, variants[: max(variants_limit, 1)], {
        "lat": _coerce_float(geo_anchor.get("lat") or row.get("geo_lat")),
        "lng": _coerce_float(geo_anchor.get("lng") or row.get("geo_lng")),
        "radius_m": _coerce_float(geo_anchor.get("radius_m")) or 120.0,
    }


def _feature_to_dict(feature: Any) -> dict:
    if feature is None:
        return {}
    if isinstance(feature, dict):
        return feature
    if hasattr(feature, "model_dump"):
        return feature.model_dump(mode="json", by_alias=True)
    return {}


def _extract_candidate_payload(feature_dict: dict) -> dict:
    properties = feature_dict.get("properties") or {}
    options = properties.get("options") or {}
    cadastre_number = _normalize_text(
        options.get("cad_num")
        or properties.get("externalKey")
        or properties.get("descr")
        or properties.get("label")
    )
    category_name = _normalize_text(properties.get("categoryName"))
    readable_address = _normalize_text(options.get("readable_address") or options.get("address"))
    cadastre_kind = _normalize_text(options.get("build_record_type_value") or category_name)
    payload = {
        "feature_id": feature_dict.get("id"),
        "cadastre_number": cadastre_number,
        "category_name": category_name,
        "cadastre_kind_raw": cadastre_kind,
        "is_building_like": _normalize_key(category_name) in BUILDING_CATEGORIES
        or _normalize_key(cadastre_kind) in {"здание", "строение", "сооружение", "объект незавершенного строительства"},
        "readable_address": readable_address,
        "purpose": _normalize_text(options.get("purpose")),
        "building_name": _normalize_text(options.get("building_name")),
        "permitted_use_name": _normalize_text(options.get("permitted_use_name")),
        "area_m2": _coerce_float(options.get("build_record_area")),
        "cadastral_value_rub": _coerce_float(options.get("cost_value")),
        "cadastral_value_per_m2": _coerce_float(options.get("cost_index")),
        "floors": _normalize_text(options.get("floors")),
        "year_built": _normalize_text(options.get("year_built")),
        "quarter_cad_number": _normalize_text(options.get("quarter_cad_number")),
        "source_feature": feature_dict,
    }
    return payload


def _house_match_score(house: str, readable_address: str) -> int:
    if not _house_token_match(house, readable_address):
        return 0
    return 15


def _house_token_match(house: str, readable_address: str) -> bool:
    house_key = _normalize_key(house).replace(" ", "")
    addr_key = _normalize_key(readable_address)
    if not house_key or not addr_key:
        return 0
    marker_tokens = _extract_address_house_tokens(addr_key)
    if marker_tokens:
        return house_key in marker_tokens
    compact_address = re.sub(r"\s+", "", addr_key)
    pattern = rf"(?<![0-9a-zа-я]){re.escape(house_key)}(?![0-9a-zа-я/])"
    return bool(re.search(pattern, compact_address))


def _extract_address_house_tokens(value: str) -> set[str]:
    text = _normalize_key(value)
    tokens: set[str] = set()
    pattern = re.compile(
        r"(?:дом|д\.?|владение|влд\.?|вл\.?)\s*№?\s*([0-9]+[а-яa-z]?(?:/[0-9]+[а-яa-z]?)?)"
    )
    for match in pattern.finditer(text):
        token = _normalize_key(match.group(1)).replace(" ", "")
        if token:
            tokens.add(token)
    return tokens


def _token_match(text: str, token: str) -> bool:
    token_key = _normalize_key(token).replace(" ", "")
    text_key = _normalize_key(text).replace(" ", "")
    return bool(token_key and text_key and token_key in text_key)


def _street_tokens(value: str) -> set[str]:
    raw = _normalize_key(value)
    for ch in ",.;:()/-":
        raw = raw.replace(ch, " ")
    tokens = {part for part in raw.split() if part and part not in STREET_TYPE_TOKENS}
    return tokens


def _street_match(readable_address: str, street: str) -> bool:
    expected = _street_tokens(street)
    actual = _street_tokens(readable_address)
    if not expected or not actual:
        return False
    return expected.issubset(actual)


def _is_residential_building_candidate(candidate: dict) -> bool:
    if not candidate.get("is_building_like"):
        return False
    blob = " ".join(
        [
            _normalize_key(candidate.get("purpose")),
            _normalize_key(candidate.get("building_name")),
            _normalize_key(candidate.get("permitted_use_name")),
        ]
    )
    if not blob.strip():
        return False
    residential_markers = (
        "многоквартирный дом",
        "жилой дом",
        "жилая застройка",
        "жилой комплекс",
        "мкд",
    )
    return any(marker in blob for marker in residential_markers)


def _candidate_score(candidate: dict, *, methods: set[str], house: str) -> int:
    score = 0
    if "search_primary" in methods:
        score += 100
    if "search_variant" in methods:
        score += 70
    if "coords" in methods:
        score += 40
    if candidate.get("is_building_like"):
        score += 50
    if candidate.get("cadastral_value_rub") not in (None, 0):
        score += 10
    score += _house_match_score(house, str(candidate.get("readable_address") or ""))
    return score


def _candidate_match_context(candidate: dict, *, locality: str, street: str, house: str) -> dict:
    readable_address = str(candidate.get("readable_address") or "")
    locality_match = _token_match(readable_address, locality)
    street_match = _street_match(readable_address, street)
    house_match = _house_token_match(house, readable_address)
    return {
        "locality_match": locality_match,
        "street_match": street_match,
        "house_match": house_match,
    }


def _policy_decision(candidates: list[dict], *, locality: str, street: str, house: str) -> dict:
    if not candidates:
        return {
            "status": "rejected",
            "reason": "no_candidates",
            "selected_cadastre_number": "",
            "selected_score": 0,
            "manual_review_count": 0,
        }

    top = candidates[0]
    top_score = int(top.get("score") or 0)
    top_building_like = bool(top.get("is_building_like"))
    top_house_match = bool((top.get("match_context") or {}).get("house_match"))
    top_street_match = bool((top.get("match_context") or {}).get("street_match"))
    top_residential_building = _is_residential_building_candidate(top)

    building_like = [item for item in candidates if item.get("is_building_like")]
    competing_buildings = [
        item
        for item in building_like[1:]
        if bool((item.get("match_context") or {}).get("house_match"))
        and int(item.get("score") or 0) >= max(top_score - 25, 0)
    ]

    if top_residential_building and top_house_match and top_street_match:
        return {
            "status": "manual_review",
            "reason": "residential_building_parent_needs_confirmation",
            "selected_cadastre_number": "",
            "selected_score": top_score,
            "manual_review_count": len(building_like),
        }

    if top_building_like and top_house_match and top_street_match and not competing_buildings and top_score >= 165:
        return {
            "status": "auto_selected",
            "reason": "single_clear_building_parent",
            "selected_cadastre_number": str(top.get("cadastre_number") or ""),
            "selected_score": top_score,
            "manual_review_count": 0,
        }

    if building_like:
        return {
            "status": "manual_review",
            "reason": "ambiguous_building_candidates" if competing_buildings else "building_candidate_needs_confirmation",
            "selected_cadastre_number": "",
            "selected_score": top_score,
            "manual_review_count": len(building_like),
        }

    return {
        "status": "rejected",
        "reason": "no_building_candidates",
        "selected_cadastre_number": "",
        "selected_score": top_score,
        "manual_review_count": 0,
    }


def _policy_decision_with_errors(candidates: list[dict], *, locality: str, street: str, house: str, errors: list[dict]) -> dict:
    if errors and not candidates:
        return {
            "status": "manual_review",
            "reason": "lookup_errors",
            "selected_cadastre_number": "",
            "selected_score": 0,
            "manual_review_count": 0,
        }
    decision = _policy_decision(candidates, locality=locality, street=street, house=house)
    if errors and decision.get("status") == "auto_selected":
        return {
            **decision,
            "status": "manual_review",
            "reason": "lookup_errors_with_candidates",
            "selected_cadastre_number": "",
            "manual_review_count": len([item for item in candidates if item.get("is_building_like")]),
        }
    return decision


def _search_candidates(
    client: Any,
    row: dict,
    *,
    variants_limit: int,
    with_coords: bool,
    top_candidates: int,
) -> dict:
    primary, variants, geo_anchor = _seed_queries(row, variants_limit=variants_limit)
    queue_key = _normalize_text(row.get("queue_building_key"))
    building_parts = queue_key.split("|")
    locality = building_parts[0] if len(building_parts) >= 1 else ""
    street = building_parts[1] if len(building_parts) >= 2 else ""
    house = building_parts[2] if len(building_parts) >= 3 else ""
    seen: dict[str, dict] = {}
    methods_map: dict[str, set[str]] = defaultdict(set)
    stats = {"search_calls": 0, "coord_calls": 0, "raw_hits": 0, "lookup_errors": 0}
    errors: list[dict] = []
    stop_due_to_transport = False

    def safe_call(method_name: str, fn, *args):
        nonlocal stop_due_to_transport
        try:
            return fn(*args)
        except Exception as exc:
            stats["lookup_errors"] += 1
            error_type = type(exc).__name__
            error_text = _normalize_text(str(exc))
            errors.append(
                {
                    "method": method_name,
                    "query": args[0] if args else "",
                    "error_type": error_type,
                    "error": error_text,
                }
            )
            if not seen and _is_transport_lookup_error(error_type=error_type, error_text=error_text):
                stop_due_to_transport = True
            return None

    def absorb(features: list[Any] | None, method: str) -> None:
        if not features:
            return
        for feature in features:
            payload = _extract_candidate_payload(_feature_to_dict(feature))
            cadastre_number = payload.get("cadastre_number")
            if not cadastre_number:
                continue
            if cadastre_number not in seen:
                seen[cadastre_number] = payload
            methods_map[cadastre_number].add(method)
            stats["raw_hits"] += 1

    if primary:
        stats["search_calls"] += 1
        absorb(safe_call("search_primary", client.search, primary), "search_primary")
    for variant in variants[1:]:
        if stop_due_to_transport and not seen:
            break
        stats["search_calls"] += 1
        absorb(safe_call("search_variant", client.search, variant), "search_variant")

    if with_coords and not (stop_due_to_transport and not seen) and geo_anchor.get("lat") is not None and geo_anchor.get("lng") is not None:
        stats["coord_calls"] += 1
        absorb(
            safe_call("coords", client.search_buildings_at_coords, float(geo_anchor["lat"]), float(geo_anchor["lng"])),
            "coords",
        )

    candidates = []
    for cadastre_number, payload in seen.items():
        methods = methods_map[cadastre_number]
        candidate = dict(payload)
        candidate["match_methods"] = sorted(methods)
        candidate["match_context"] = _candidate_match_context(candidate, locality=locality, street=street, house=house)
        candidate["score"] = _candidate_score(candidate, methods=methods, house=house)
        candidates.append(candidate)
    candidates.sort(
        key=lambda item: (
            -int(item.get("score") or 0),
            -int(bool(item.get("is_building_like"))),
            -int(bool(item.get("cadastral_value_rub"))),
            str(item.get("cadastre_number") or ""),
        )
    )

    decision = _policy_decision_with_errors(candidates, locality=locality, street=street, house=house, errors=errors)
    return {
        "queue_building_key": row.get("queue_building_key"),
        "display_address": row.get("display_address"),
        "lookup_queries": {
            "primary": primary,
            "variants": variants,
            "geo_anchor": geo_anchor,
        },
        "nspd_lookup_stats": {
            **stats,
            "unique_candidates": len(candidates),
        },
        "lookup_errors": errors,
        "policy_decision": decision,
        "candidates": candidates[: max(top_candidates, 1)],
    }


def build_nspd_candidate_rows(
    seed_rows: list[dict],
    *,
    client: Any,
    variants_limit: int = DEFAULT_VARIANTS_LIMIT,
    with_coords: bool = True,
    top_candidates: int = DEFAULT_TOP_CANDIDATES,
) -> tuple[list[dict], dict]:
    rows_out: list[dict] = []
    report = {
        "generated_at": _utc_now_iso(),
        "rows_seen": 0,
        "rows_with_candidates": 0,
        "rows_without_candidates": 0,
        "policy_counts": {"auto_selected": 0, "manual_review": 0, "rejected": 0},
        "search_calls": 0,
        "coord_calls": 0,
        "lookup_errors": 0,
        "top_hits": [],
    }
    for seed_row in seed_rows:
        report["rows_seen"] += 1
        item = _search_candidates(
            client,
            seed_row,
            variants_limit=variants_limit,
            with_coords=with_coords,
            top_candidates=top_candidates,
        )
        report["search_calls"] += int((item.get("nspd_lookup_stats") or {}).get("search_calls") or 0)
        report["coord_calls"] += int((item.get("nspd_lookup_stats") or {}).get("coord_calls") or 0)
        report["lookup_errors"] += int((item.get("nspd_lookup_stats") or {}).get("lookup_errors") or 0)
        if item["candidates"]:
            report["rows_with_candidates"] += 1
        else:
            report["rows_without_candidates"] += 1
        decision = (item.get("policy_decision") or {}).get("status") or "rejected"
        report["policy_counts"][decision] = int(report["policy_counts"].get(decision, 0) or 0) + 1
        rows_out.append(item)

    ranked = sorted(rows_out, key=lambda item: -int((item.get("nspd_lookup_stats") or {}).get("unique_candidates") or 0))
    report["top_hits"] = [
        {
            "display_address": item.get("display_address"),
            "unique_candidates": (item.get("nspd_lookup_stats") or {}).get("unique_candidates"),
            "top_candidate": ((item.get("candidates") or [{}])[0]).get("cadastre_number") if item.get("candidates") else "",
            "decision": ((item.get("policy_decision") or {}).get("status") or ""),
        }
        for item in ranked[:10]
    ]
    return rows_out, report


def _build_client(*, cache_folder: Path, timeout: int, retries: int, proxy: str | None, trust_env: bool = False):
    from pynspd import Nspd

    kwargs: dict[str, Any] = {
        "client_timeout": timeout,
        "client_retries": retries,
        "cache_folder_path": cache_folder,
        "trust_env": bool(trust_env),
    }
    if proxy:
        kwargs["client_proxy"] = proxy
    return Nspd(**kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect NSPD candidate cadastre numbers for MO shortlist groups.")
    parser.add_argument("--seed", default=str(DEFAULT_SEED))
    parser.add_argument("--output-jsonl", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--output-report", default=str(DEFAULT_REPORT))
    parser.add_argument("--cache-folder", default=str(DEFAULT_CACHE_FOLDER))
    parser.add_argument("--variants-limit", type=int, default=DEFAULT_VARIANTS_LIMIT)
    parser.add_argument("--top-candidates", type=int, default=DEFAULT_TOP_CANDIDATES)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--client-timeout", type=int, default=20)
    parser.add_argument("--client-retries", type=int, default=1)
    parser.add_argument("--client-proxy", default="")
    parser.add_argument("--no-coords", action="store_true")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    output_path = Path(args.output_jsonl)
    report_path = Path(args.output_report)
    cache_folder = Path(args.cache_folder)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    cache_folder.mkdir(parents=True, exist_ok=True)

    seed_rows = list(_iter_seed_rows(seed_path))
    if int(args.max_rows or 0) > 0:
        seed_rows = seed_rows[: int(args.max_rows)]

    client = _build_client(
        cache_folder=cache_folder,
        timeout=int(args.client_timeout or 20),
        retries=int(args.client_retries or 1),
        proxy=str(args.client_proxy or "").strip() or None,
    )
    rows_out, report = build_nspd_candidate_rows(
        seed_rows,
        client=client,
        variants_limit=max(int(args.variants_limit or DEFAULT_VARIANTS_LIMIT), 1),
        with_coords=not bool(args.no_coords),
        top_candidates=max(int(args.top_candidates or DEFAULT_TOP_CANDIDATES), 1),
    )
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows_out:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = dict(report)
    report["seed_path"] = str(seed_path)
    report["output_jsonl"] = str(output_path)
    report["cache_folder"] = str(cache_folder)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("MO_NSPD_CANDIDATES_BUILT")
    for key, value in report.items():
        if isinstance(value, list):
            print(f"  {key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()
