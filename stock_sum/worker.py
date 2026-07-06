"""Short-lived child worker for heavy stock-sum tasks."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import argparse
import asyncio
import json
import sys

from stock_sum.api.jobs import (
    HttpJobManager,
    SocialReportJobOptions,
    Sec13FReportJobOptions,
    StatisticJobOptions,
    TradingReportJobOptions,
)
from stock_sum.config.models import AppConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one stock-sum worker request.")
    parser.add_argument("--request", required=True, help="Path to worker-request.json.")
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run_request(Path(args.request)))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


async def _run_request(path: Path) -> int:
    request = json.loads(path.read_text(encoding="utf-8"))
    if request.get("schema_version") != 1:
        raise ValueError("Unsupported worker request schema.")
    config = AppConfig.model_validate(request["config"])
    operation = request["operation"]
    payload = request.get("payload") or {}

    if operation.startswith("http_"):
        await _run_http_operation(config, operation, request["job_id"], payload)
        return 0
    await _run_cli_operation(config, operation, payload)
    return 0


async def _run_http_operation(config: AppConfig, operation: str, job_id: str, payload: dict[str, Any]) -> None:
    manager = HttpJobManager(config, use_subprocess_workers=False, recover_stale_jobs=False)
    if operation == "http_social_report":
        await manager._run_social_report_job_in_process(job_id, SocialReportJobOptions(**payload["options"]))
        return
    if operation == "http_trading_report":
        await manager._run_trading_report_job_in_process(job_id, TradingReportJobOptions(**payload["options"]))
        return
    if operation == "http_13f_report":
        await manager._run_13f_report_job_in_process(job_id, Sec13FReportJobOptions(**payload["options"]))
        return
    if operation == "http_statistic":
        await manager._run_statistic_job_in_process(job_id, StatisticJobOptions(**payload["options"]))
        return
    if operation == "http_collect":
        await manager._run_collect_job_in_process(job_id)
        return
    if operation == "http_render_cached_social_report":
        await _run_render_cached_social_report(manager, job_id, payload)
        return
    if operation == "http_render_coalesced_social_report":
        await _run_render_coalesced_social_report(manager, job_id, payload)
        return
    raise ValueError(f"Unknown HTTP worker operation: {operation}")


async def _run_render_cached_social_report(manager: HttpJobManager, job_id: str, payload: dict[str, Any]) -> None:
    try:
        cache_hit = manager.get_job(str(payload["cache_hit_job_id"]))
        if cache_hit is None:
            raise RuntimeError(f"Cached report job not found: {payload['cache_hit_job_id']}")
        manager._write_cached_social_report_artifacts(job_id, cache_hit, SocialReportJobOptions(**payload["options"]))
    except Exception as exc:
        manager._mark_failed(job_id, str(exc))
    finally:
        await manager._run_retention(job_id)
        manager._refresh_memory_status(job_id)


async def _run_render_coalesced_social_report(manager: HttpJobManager, job_id: str, payload: dict[str, Any]) -> None:
    try:
        leader = manager.get_job(str(payload["leader_job_id"]))
        if leader is None:
            raise RuntimeError(f"Coalesced report leader not found: {payload['leader_job_id']}")
        manager._write_coalesced_report_artifacts(
            job_id=job_id,
            leader=leader,
            options=SocialReportJobOptions(**payload["options"]),
            wait_seconds=int(payload.get("wait_seconds") or 0),
        )
    except Exception as exc:
        manager._mark_failed(job_id, str(exc))
    finally:
        await manager._run_retention(job_id)
        manager._refresh_memory_status(job_id)


async def _run_cli_operation(config: AppConfig, operation: str, payload: dict[str, Any]) -> None:
    if operation == "cli_collect":
        await _cli_collect(config, payload)
        return
    if operation == "cli_llm_summarize":
        await _cli_llm_summarize(config, payload)
        return
    if operation == "cli_llm_analyze":
        await _cli_llm_analyze(config, payload)
        return
    raise ValueError(f"Unknown CLI worker operation: {operation}")


async def _cli_collect(config: AppConfig, payload: dict[str, Any]) -> None:
    from stock_sum.core.context import RuntimeContext
    from stock_sum.core.pipeline import ReportPipeline

    pipeline = ReportPipeline(RuntimeContext(config=config))
    if payload.get("collector"):
        result = await pipeline.collect_collector(str(payload["collector"]))
        await _retention_after_pipeline(config)
        print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        return
    result = await pipeline.collect_sources(scope="social")
    await _retention_after_pipeline(config)
    print(json.dumps(_pipeline_result_to_jsonable(result), indent=2, ensure_ascii=False))


async def _cli_llm_summarize(config: AppConfig, payload: dict[str, Any]) -> None:
    from stock_sum.llm.registry import build_llm_client
    from stock_sum.reports.summary_input import SummaryInputBuilder
    from stock_sum.storage.sqlite import SQLiteStorageRepository

    payload_path = payload.get("payload_path")
    if payload_path:
        payload_data = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    else:
        repository = SQLiteStorageRepository(config.storage.sqlite_path)
        summary_input = await SummaryInputBuilder(config=config, repository=repository).build(download_images=False)
        payload_data = summary_input.to_dict(
            mode="compact",
            max_images_per_post=int(payload["max_images_per_post"]),
            max_images_total=int(payload["max_images_total"]),
        )
    summary = await build_llm_client(config.llm).summarize(payload_data, instructions=payload.get("instructions"))
    response_data = {
        "report_type": "social",
        "provider": config.llm.provider,
        "model": summary.model,
        "summary_text": summary.text,
        "summary": summary.metadata.get("parsed"),
        "input_media": payload_data.get("media", {}) if isinstance(payload_data, dict) else {},
        "metadata": {key: value for key, value in summary.metadata.items() if key != "parsed"},
    }
    output = Path(payload["output_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(response_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output}")


async def _cli_llm_analyze(config: AppConfig, payload: dict[str, Any]) -> None:
    from stock_sum.llm.analysis import LLMAnalysisService
    from stock_sum.llm.registry import build_llm_client
    from stock_sum.reports.summary_input import SummaryInputBuilder
    from stock_sum.storage.sqlite import SQLiteStorageRepository

    repository = SQLiteStorageRepository(config.storage.sqlite_path)
    summary_input = await SummaryInputBuilder(config=config, repository=repository).build(download_images=False)
    result = await LLMAnalysisService(
        config=config,
        repository=repository,
        llm_client=build_llm_client(config.llm),
    ).analyze(
        summary_input,
        instructions=payload.get("instructions"),
        max_images_per_post=int(payload["max_images_per_post"]),
        max_images_total=int(payload["max_images_total"]),
    )
    response_data = {
        "report_type": "social",
        "provider": config.llm.provider,
        "model": result.model,
        "summary_text": json.dumps(result.summary, ensure_ascii=False),
        "summary": result.summary,
        "metadata": {
            "analysis_run_id": result.analysis_run_id,
            "prompt_version": result.prompt_version,
            "chunk_count": result.chunk_count,
            "succeeded_count": result.succeeded_count,
            "failed_count": result.failed_count,
        },
        "pipeline_warnings": [asdict(warning) for warning in result.warnings],
    }
    output = Path(payload["output_path"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(response_data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output}")


async def _retention_after_pipeline(config: AppConfig) -> None:
    from stock_sum.retention import DataRetentionService

    if config.retention.prune_after_pipeline:
        summary = await DataRetentionService(config).prune()
        if summary.bytes_deleted > 0 or summary.errors:
            print(json.dumps({"retention": summary.to_dict()}, indent=2, ensure_ascii=False))


def _pipeline_result_to_jsonable(result: Any) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


if __name__ == "__main__":
    raise SystemExit(main())
