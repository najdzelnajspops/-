"""Серверный аудит-лог каждого вызова /generate и /review (веб-UI).

Прямой запрос пользователя (2026-09-19): «должны сохраняться логи каждой
генерации, так как потом могут с нас спросить, что сервис этого не
порекомендовал». Это НЕЗАВИСИМО от того, что пользователь решит скрыть на
экране/при печати (см. `pipeline/web/static/app.js`, переключатель «скрыть
рекомендации сервиса») — здесь всегда пишется ПОЛНЫЙ, нетронутый ответ,
который реально ушёл клиенту.

Формат — JSONL (один JSON-объект на строку), один файл на UTC-сутки. Простой
синхронный дозапись файла — нагрузка хакатон-демо не оправдывает БД/очередь.
Каталог смонтирован как volume в docker-compose.yml (`./data:/app/data`),
переживает перезапуск контейнера. Не публикуется в git (см. .gitignore).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

AUDIT_DIR = Path(__file__).resolve().parents[2] / "data" / "web_audit"


def log_event(
    kind: Literal["generate", "review"],
    demo_id: str,
    territory_category: str,
    request_payload: dict[str, Any],
    response_payload: dict[str, Any],
    audit_dir: Path | None = None,
) -> None:
    # audit_dir читается из модульной переменной ВНУТРИ тела функции (не как
    # значение параметра по умолчанию) — иначе monkeypatch/подмена
    # audit_log.AUDIT_DIR в тестах не сработала бы: значения по умолчанию
    # в Python вычисляются один раз при определении функции, а не при вызове.
    target_dir = audit_dir if audit_dir is not None else AUDIT_DIR
    now = datetime.now(timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "kind": kind,
        "demo_id": demo_id,
        "territory_category": territory_category,
        "request": request_payload,
        "response": response_payload,
    }
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
