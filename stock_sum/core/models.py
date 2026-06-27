"""Shared domain data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class RawItem:
    """A raw item collected from an external source."""

    source_id: str
    source_type: str
    url: str | None
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Summary:
    """LLM-generated summary payload."""

    text: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """Rendered report ready for delivery."""

    profile: str
    subject: str
    body: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineRun:
    """A single pipeline execution."""

    profile: str
    run_id: str = field(default_factory=lambda: str(uuid4()))
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
