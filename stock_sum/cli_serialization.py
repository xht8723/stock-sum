"""JSON serialization helpers for CLI command output."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult


def collection_run_to_jsonable(result: CollectionRunResult) -> dict[str, Any]:
    return asdict(result)


def pipeline_result_to_jsonable(result: PipelineCollectionResult) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


_collection_run_to_jsonable = collection_run_to_jsonable
_pipeline_result_to_jsonable = pipeline_result_to_jsonable
