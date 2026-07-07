"""Persisted HTTP job status helpers."""

from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from stock_sum.api.job_models import HttpJobRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def job_sort_datetime(record: HttpJobRecord) -> datetime:
    return (
        parse_utc_datetime(record.finished_at)
        or parse_utc_datetime(record.updated_at)
        or parse_utc_datetime(record.created_at)
        or datetime.min.replace(tzinfo=timezone.utc)
    )


def job_record_from_dict(data: dict[str, Any]) -> HttpJobRecord:
    allowed = {item.name for item in fields(HttpJobRecord)}
    return HttpJobRecord(**{key: value for key, value in data.items() if key in allowed})


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


_utc_now = utc_now
_parse_utc_datetime = parse_utc_datetime
_job_sort_datetime = job_sort_datetime
_job_record_from_dict = job_record_from_dict
