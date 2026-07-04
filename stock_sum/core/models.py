"""Shared domain data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


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
class ProviderApiResponse:
    """Captured raw provider API/tool response for storage and debugging."""

    provider: str
    tool_name: str
    request_arguments: dict[str, Any]
    raw_response_text: str
    parsed_rows: list[dict[str, Any]]
    row_count: int
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class Summary:
    """LLM-generated summary payload."""

    text: str
    model: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawItemSaveResult:
    """Persistence counts for a raw item batch."""

    source_type: str
    collected_count: int
    inserted_count: int
    updated_count: int


@dataclass(frozen=True)
class CollectionRunResult:
    """Result for one collector execution."""

    run_id: str
    collector_id: str
    source_type: str
    status: str
    collected_count: int
    inserted_count: int
    updated_count: int
    sqlite_path: str
    error: str | None = None
    warnings: list[PipelineSectionWarning] = field(default_factory=list)


@dataclass(frozen=True)
class PipelineSectionWarning:
    """Recoverable failure for one report pipeline section."""

    section: str
    source_id: str
    phase: str
    message: str
    recoverable: bool = True


@dataclass(frozen=True)
class PipelineCollectionResult:
    """Collection-only result for a report profile run."""

    profile: str
    runs: list[CollectionRunResult]
    warnings: list[PipelineSectionWarning] = field(default_factory=list)

    @property
    def collected_count(self) -> int:
        """Total collected items across collector runs."""

        return sum(run.collected_count for run in self.runs)

    @property
    def inserted_count(self) -> int:
        """Total inserted items across collector runs."""

        return sum(run.inserted_count for run in self.runs)

    @property
    def updated_count(self) -> int:
        """Total updated or duplicate items across collector runs."""

        return sum(run.updated_count for run in self.runs)
