"""Persistence model placeholders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class StoredRecord:
    """Minimal stored record placeholder."""

    record_id: str
    kind: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
