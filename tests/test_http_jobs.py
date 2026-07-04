"""HTTP job manager fail-safe behavior tests."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stock_sum.api.jobs import HttpJobManager, ReportJobOptions, Sec13FReportJobOptions, TradingReportJobOptions
from stock_sum.config.loader import load_config
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult, PipelineSectionWarning, Summary
from stock_sum.retention import RetentionSummary
from stock_sum.storage.models import StoredCollectionRun, StoredHousePtrTradeRow, StoredSec13FHolding, StoredXPost


async def test_report_job_succeeds_with_collection_warning(tmp_path) -> None:
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                profile="default",
                runs=[],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="reddit.wallstreetbets",
                        phase="collecting",
                        message="temporary reddit failure",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="discord"))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="discord"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert status.artifact_path is not None
    assert len(status.warnings) == 1
    summary = Path(status.summary_path or "").read_text(encoding="utf-8")
    assert "pipeline_warnings" in summary
    assert "failed_sections" in summary
    assert "temporary reddit failure" in summary


async def test_report_job_fails_when_no_usable_social_data(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(
            PipelineCollectionResult(
                profile="default",
                runs=[
                    CollectionRunResult(
                        run_id="run-1",
                        collector_id="x.missing",
                        source_type="raw_x_post",
                        status="failed",
                        collected_count=0,
                        inserted_count=0,
                        updated_count=0,
                        sqlite_path=str(tmp_path / "stock_sum.sqlite3"),
                        error="x failed",
                    )
                ],
                warnings=[
                    PipelineSectionWarning(
                        section="collector",
                        source_id="x.missing",
                        phase="collecting",
                        message="x failed",
                    )
                ],
            )
        ),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[]),
        llm_client_factory=lambda: llm,
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="html"))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="html"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "failed"
    assert "no usable source data" in str(status.error)
    assert "x.missing" in str(status.error)
    assert status.artifact_path is None
    assert llm.calls == 0
    assert status.warnings[0]["source_id"] == "x.missing"


async def test_social_report_ignores_house_only_data_and_requires_social_data(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[_house_row()]),
        llm_client_factory=lambda: llm,
    )
    job = manager.create_report_job("default", ReportJobOptions(mode="text"))

    await manager.run_report_job(job.job_id, ReportJobOptions(mode="text"))

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "failed"
    assert "no usable source data" in str(status.error)
    assert llm.calls == 0


async def test_trading_report_succeeds_with_house_data_and_skips_llm(tmp_path) -> None:
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[_house_row()]),
        llm_client_factory=lambda: llm,
    )
    options = TradingReportJobOptions(mode="text", name="Jane")
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    assert llm.calls == 0
    artifact = Path(status.artifact_path or "").read_text(encoding="utf-8")
    assert "OFFICIAL TRADING DISCLOSURES" in artifact
    assert "Jane Doe" in artifact


async def test_trading_report_filters_by_asset_type_and_ticker(tmp_path) -> None:
    repository = FakeRepository(
        with_social_data=False,
        house_rows=[
            _house_row(doc_id="amzn", asset="Amazon.com, Inc. - Common Stock (AMZN) [ST]", asset_type_code="ST", stock_ticker="AMZN"),
            _house_row(doc_id="bond", asset="US Treasury Note [GS]", asset_type_code="GS", stock_ticker=None),
        ],
    )
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: repository,
        llm_client_factory=lambda: FakeLLM(),
    )
    options = TradingReportJobOptions(mode="json", asset_type="st", ticker="amzn")
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    assert repository.last_house_filters["asset_type"] == "st"
    assert repository.last_house_filters["ticker"] == "amzn"
    status = manager.get_job(job.job_id)
    assert status is not None
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["house_ptr"][0]["asset_type_code"] == "ST"
    assert summary["house_ptr"][0]["stock_ticker"] == "AMZN"


async def test_13f_report_job_renders_matching_holdings_without_llm(tmp_path) -> None:
    row = StoredSec13FHolding(
        dataset_id="dataset-1",
        dataset_label="2026 March April May 13F",
        accession_number="0001234567-26-000001",
        cik="0001067983",
        manager_name="Berkshire Hathaway Inc",
        filing_date="31-MAY-2026",
        filing_date_utc="2026-05-31",
        period_of_report="31-MAR-2026",
        period_of_report_utc="2026-03-31",
        info_table_sk="1",
        issuer="NVIDIA CORP",
        title_of_class="COM",
        cusip="67066G104",
        figi="BBG000BBJQV0",
        value=1000,
        ssh_prn_amt=50,
        ssh_prn_type="SH",
        put_call="CALL",
        investment_discretion="SOLE",
        other_manager=None,
        voting_auth_sole=50,
        voting_auth_shared=0,
        voting_auth_none=0,
        filing_url="https://www.sec.gov/Archives/edgar/data/1067983/000123456726000001/0001234567-26-000001.txt",
        raw_metadata={},
    )
    repository = FakeRepository(with_social_data=False, sec_13f_rows=[row])
    manager = HttpJobManager(
        _test_config(tmp_path),
        repository_factory=lambda: repository,
        llm_client_factory=lambda: (_ for _ in ()).throw(AssertionError("LLM should not be used")),
    )
    options = Sec13FReportJobOptions(mode="json", issuer="nvidia", limit=20)
    job = manager.create_13f_report_job(options)

    await manager.run_13f_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert summary["sec_13f"][0]["issuer"] == "NVIDIA CORP"
    assert repository.last_sec_13f_filters["issuer"] == "nvidia"


async def test_trading_report_sorts_rows_by_transaction_date(tmp_path) -> None:
    old_row = _house_row(doc_id="old", asset="OLD", transaction_date="2026-06-01")
    new_row = _house_row(doc_id="new", asset="NEW", transaction_date="2026-06-30")
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: FakePipeline(_successful_collection_result()),
        repository_factory=lambda: FakeRepository(with_social_data=False, house_rows=[old_row, new_row]),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = TradingReportJobOptions(mode="json", days=90)
    job = manager.create_trading_report_job(options)

    await manager.run_trading_report_job(job.job_id, options)

    status = manager.get_job(job.job_id)
    assert status is not None
    assert status.status == "succeeded"
    summary = json.loads(Path(status.summary_path or "").read_text(encoding="utf-8"))
    assert [row["asset"] for row in summary["house_ptr"]] == ["NEW", "OLD"]


async def test_report_job_uses_recent_cache_and_rerenders_requested_mode(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    renderer_calls: list[tuple[str, str, str]] = []
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
        renderer_factory=lambda title: FakeRenderer(title, renderer_calls),
    )
    first_options = ReportJobOptions(mode="html", detail="full")
    first_job = manager.create_report_job("default", first_options)
    await manager.run_report_job(first_job.job_id, first_options)

    second_options = ReportJobOptions(mode="discord", detail="minimum")
    second_job = manager.create_report_job("default", second_options)
    await manager.run_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.cache_hit is True
    assert second_status.cached_from_job_id == first_job.job_id
    assert second_status.cache_age_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert Path(second_status.artifact_path).read_text(encoding="utf-8") == "Market Social Digest:discord:minimum"
    assert renderer_calls == [("Market Social Digest", "html", "full"), ("Market Social Digest", "discord", "minimum")]
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_report_job_cache_miss_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", first_options)
    await manager.run_report_job(first_job.job_id, first_options)

    second_options = ReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    second_job = manager.create_report_job("default", second_options)
    await manager.run_report_job(second_job.job_id, second_options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert second_status.cached_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_cache_expires_after_ttl(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    first_status.finished_at = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    manager._save(first_status)

    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_ignores_cache_when_cached_summary_is_missing(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    first_status = manager.get_job(first_job.job_id)
    assert first_status is not None
    assert first_status.summary_path is not None
    Path(first_status.summary_path).unlink()

    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_does_not_use_cache_when_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    llm = FakeLLM()
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"report_cache_ttl_seconds": 0})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_report_job_runs_retention_after_regular_and_cache_hit_jobs(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result())
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    await manager.run_report_job(first_job.job_id, options)
    second_job = manager.create_report_job("default", options)
    await manager.run_report_job(second_job.job_id, options)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert retention.calls == 2
    assert first_status is not None
    assert second_status is not None
    assert first_status.cleanup_result is not None
    assert second_status.cache_hit is True
    assert second_status.cleanup_result is not None


async def test_identical_concurrent_report_jobs_coalesce_to_one_pipeline_run(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html", detail="full")
    second_options = ReportJobOptions(mode="discord", detail="minimum")
    first_job = manager.create_report_job("default", first_options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, first_options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", second_options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, second_options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.coalesced_wait_seconds is not None
    assert second_status.artifact_path is not None
    assert second_status.artifact_path.endswith("report.md")
    assert second_status.cache_hit is False
    assert pipeline.calls == 1
    assert llm.calls == 1


async def test_concurrent_report_jobs_do_not_coalesce_when_content_options_change(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    llm = FakeLLM()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: llm,
    )
    first_options = ReportJobOptions(mode="html")
    second_options = ReportJobOptions(mode="html", instructions="Focus on semiconductor names.")
    first_job = manager.create_report_job("default", first_options)
    second_job = manager.create_report_job("default", second_options)

    await asyncio.gather(
        manager.run_report_job(first_job.job_id, first_options),
        manager.run_report_job(second_job.job_id, second_options),
    )

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "succeeded"
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id is None
    assert pipeline.calls == 2
    assert llm.calls == 2


async def test_coalesced_report_job_fails_when_leader_fails(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05, fail=True)
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    first_status = manager.get_job(first_job.job_id)
    second_status = manager.get_job(second_job.job_id)
    assert first_status is not None
    assert second_status is not None
    assert first_status.status == "failed"
    assert second_status.status == "failed"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert f"Coalesced report leader {first_job.job_id} failed" in str(second_status.error)
    assert pipeline.calls == 1


async def test_inflight_report_coalescing_can_be_disabled(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    config = _test_config(tmp_path)
    manager = HttpJobManager(
        config.model_copy(update={"server": config.server.model_copy(update={"coalesce_inflight_reports": False})}),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    second_job = manager.create_report_job("default", options)

    await asyncio.gather(
        manager.run_report_job(first_job.job_id, options),
        manager.run_report_job(second_job.job_id, options),
    )

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.coalesced_from_job_id is None
    assert second_status.cache_hit is False
    assert pipeline.calls == 2


async def test_coalesced_report_job_runs_retention_after_writing_artifact(tmp_path) -> None:
    pipeline = FakePipeline(_successful_collection_result(), delay_seconds=0.05)
    retention = FakeRetentionService()
    manager = HttpJobManager(
        _test_config(tmp_path),
        pipeline_factory=lambda: pipeline,
        repository_factory=lambda: FakeRepository(with_social_data=True),
        llm_client_factory=lambda: FakeLLM(),
        retention_service_factory=lambda: retention,
    )
    options = ReportJobOptions(mode="html")
    first_job = manager.create_report_job("default", options)
    first_task = asyncio.create_task(manager.run_report_job(first_job.job_id, options))
    await asyncio.sleep(0.01)
    second_job = manager.create_report_job("default", options)
    second_task = asyncio.create_task(manager.run_report_job(second_job.job_id, options))

    await asyncio.gather(first_task, second_task)

    second_status = manager.get_job(second_job.job_id)
    assert second_status is not None
    assert second_status.status == "succeeded"
    assert second_status.coalesced_from_job_id == first_job.job_id
    assert second_status.cleanup_result is not None
    assert retention.calls == 2


async def test_completed_jobs_older_than_retention_are_evicted_from_memory_and_reload_from_disk(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config)
    job = manager.create_collect_job("default")
    status = manager.get_job(job.job_id)
    assert status is not None
    status.status = "succeeded"
    status.phase = "succeeded"
    status.finished_at = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    status.artifact_path = str(tmp_path / "artifact.json")
    manager._save(status)

    evicted = manager._refresh_memory_status()

    assert evicted == 1
    assert job.job_id not in manager._jobs
    reloaded = manager.get_job(job.job_id)
    assert reloaded is not None
    assert reloaded.job_id == job.job_id
    assert reloaded.in_memory_jobs == 1


async def test_in_memory_job_cap_evicts_oldest_finished_jobs_and_keeps_active_jobs(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = HttpJobManager(config)
    finished_ids: list[str] = []
    for index in range(4):
        job = manager.create_collect_job("default")
        status = manager.get_job(job.job_id)
        assert status is not None
        status.status = "succeeded"
        status.phase = "succeeded"
        status.finished_at = (datetime.now(timezone.utc) - timedelta(minutes=10 - index)).isoformat()
        status.artifact_path = str(tmp_path / f"artifact-{index}.json")
        manager._save(status)
        finished_ids.append(job.job_id)
    running = manager.create_collect_job("default")
    manager._mark_running(running.job_id, phase="running")
    manager.config = config.model_copy(update={"server": config.server.model_copy(update={"max_in_memory_jobs": 3})})

    evicted = manager._refresh_memory_status(protected_job_ids={running.job_id})

    assert evicted == 2
    assert running.job_id in manager._jobs
    assert finished_ids[0] not in manager._jobs
    assert len(manager._jobs) == 3


async def test_inflight_leader_is_preserved_when_memory_cache_is_pruned(tmp_path) -> None:
    config = _test_config(tmp_path)
    config = config.model_copy(update={"server": config.server.model_copy(update={"max_in_memory_jobs": 1})})
    manager = HttpJobManager(config)
    leader = manager.create_report_job("default", ReportJobOptions(mode="html"))
    old = manager.create_collect_job("default")
    old_status = manager.get_job(old.job_id)
    assert old_status is not None
    old_status.status = "succeeded"
    old_status.phase = "succeeded"
    old_status.finished_at = datetime.now(timezone.utc).isoformat()
    manager._save(old_status)
    is_leader, _ = await manager._join_or_register_inflight_report(leader.job_id, leader.cache_key)
    assert is_leader is True

    manager._refresh_memory_status()

    assert leader.job_id in manager._jobs
    assert old.job_id not in manager._jobs


async def test_completed_memory_record_is_evicted_when_status_file_is_deleted(tmp_path) -> None:
    manager = HttpJobManager(_test_config(tmp_path))
    job = manager.create_collect_job("default")
    status = manager.get_job(job.job_id)
    assert status is not None
    status.status = "succeeded"
    status.phase = "succeeded"
    status.finished_at = datetime.now(timezone.utc).isoformat()
    manager._save(status)
    (manager._job_dir(job.job_id) / "status.json").unlink()

    evicted = manager._refresh_memory_status()

    assert evicted == 1
    assert job.job_id not in manager._jobs


class FakePipeline:
    def __init__(self, result: PipelineCollectionResult, *, delay_seconds: float = 0, fail: bool = False) -> None:
        self.result = result
        self.delay_seconds = delay_seconds
        self.fail = fail
        self.calls = 0

    async def run_report(self, profile: str, *, collector_ids=None) -> PipelineCollectionResult:
        self.calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("pipeline failed")
        return self.result

    async def collect_collector(self, collector_id: str, *, profile: str | None = None, raise_on_error: bool = True):
        self.calls += 1
        return CollectionRunResult(
            run_id="house-run",
            collector_id=collector_id,
            source_type="house_ptr_disclosure",
            status="succeeded",
            collected_count=1,
            inserted_count=1,
            updated_count=0,
            sqlite_path="stock_sum.sqlite3",
        )


class FakeRepository:
    def __init__(self, *, with_social_data: bool, house_rows=None, sec_13f_rows=None) -> None:
        self.with_social_data = with_social_data
        self.house_rows = house_rows or []
        self.sec_13f_rows = sec_13f_rows or []
        self.x_analysis_rows = []
        self.reddit_post_analysis_rows = []
        self.reddit_comment_analysis_rows = []
        self.last_house_filters = {}
        self.last_sec_13f_filters = {}

    async def list_collection_runs(self, *, profile: str | None = None, limit: int = 20):
        if profile == "13f":
            return [
                StoredCollectionRun(
                    run_id="sec-run",
                    profile="13f",
                    collector_id="sec.13f",
                    source_type="sec_13f_dataset",
                    status="succeeded",
                    started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    collected_count=1,
                    inserted_count=1,
                    updated_count=0,
                    error_text=None,
                )
            ]
        return [
            StoredCollectionRun(
                run_id="house-run",
                profile="trading",
                collector_id="house.ptr",
                source_type="house_ptr_disclosure",
                status="succeeded",
                started_at=datetime.now(timezone.utc).isoformat(),
                finished_at=datetime.now(timezone.utc).isoformat(),
                collected_count=1,
                inserted_count=1,
                updated_count=0,
                error_text=None,
            )
        ]

    async def read_x_posts(self, *, handles=None, since_posted_at=None, collector_id=None, profile=None, since=None, limit=50):
        if not self.with_social_data:
            return []
        return [
            StoredXPost(
                status_id="1",
                handle="aleabitoreddit",
                author_handle="aleabitoreddit",
                author_name="Serenity",
                posted_at_text="2999-01-01T00:00:00+00:00",
                url="https://x.com/aleabitoreddit/status/1",
                text="market signal",
                reply_count=1,
                repost_count=2,
                like_count=3,
                quote_count=4,
                view_count=5,
                raw_metadata={},
                collected_at="2026-06-29T00:00:00+00:00",
            )
        ]

    async def read_reddit_posts(self, *, subreddits=None, since_posted_at=None, collector_id=None, profile=None, since=None, limit=50):
        return []

    async def existing_house_ptr_doc_ids(self, *, year=None):
        return set()

    async def read_house_ptr_trades(
        self,
        *,
        name_contains=None,
        transaction_start=None,
        transaction_end=None,
        asset_type=None,
        ticker=None,
        limit=None,
    ):
        self.last_house_filters = {"asset_type": asset_type, "ticker": ticker}
        rows = list(self.house_rows)
        if asset_type:
            rows = [row for row in rows if (row.asset_type_code or "").upper() == asset_type.upper()]
        if ticker:
            rows = [row for row in rows if (row.stock_ticker or "").upper() == ticker.upper()]
        return rows if limit is None else rows[:limit]

    async def read_sec_13f_holdings(
        self,
        *,
        manager=None,
        cik=None,
        accession_number=None,
        issuer=None,
        cusip=None,
        figi=None,
        put_call=None,
        period_start=None,
        period_end=None,
        filing_start=None,
        filing_end=None,
        min_value=None,
        min_shares=None,
        limit=20,
    ):
        self.last_sec_13f_filters = {"manager": manager, "issuer": issuer, "cusip": cusip, "limit": limit}
        rows = list(self.sec_13f_rows)
        if issuer:
            rows = [row for row in rows if issuer.lower() in (row.issuer or "").lower()]
        if manager:
            rows = [row for row in rows if manager.lower() in (row.manager_name or "").lower()]
        if cusip:
            rows = [row for row in rows if (row.cusip or "").upper() == cusip.upper()]
        return rows[:limit]

    async def start_llm_analysis_run(self, **kwargs):
        return None

    async def finish_llm_analysis_run(self, **kwargs):
        return None

    async def save_llm_x_post_analyses(self, rows):
        self.x_analysis_rows.extend(rows)

    async def save_llm_reddit_post_analyses(self, rows):
        self.reddit_post_analysis_rows.extend(rows)

    async def save_llm_reddit_comment_analyses(self, rows):
        self.reddit_comment_analysis_rows.extend(rows)

    async def read_llm_analysis_report(self, *, profile: str, analysis_run_id: str | None = None):
        posts = [
            {
                "source_ref": row["source_ref"],
                "source_id": row["status_id"],
                "title": row["summary"],
                "post_summary": row["summary"],
                "sentiment": row["sentiment"],
                "tags": ["market", "social", "signal", "risk", "watch"],
                "interpretation": row["interpretation"],
                "confidence": row["confidence"],
                "urls": [row["url"]],
            }
            for row in self.x_analysis_rows
        ]
        return {
            "x_reports": [{"handle": "aleabitoreddit", "overall_summary": ["summary"], "posts": posts}],
            "reddit_report": {"overall_summary": [], "posts": []},
        }


class FakeLLM:
    provider = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    async def summarize(self, payload, *, instructions=None) -> Summary:
        return await self.complete_json([])

    async def complete_json(self, messages) -> Summary:
        self.calls += 1
        return Summary(
            text='{"source":"x","posts":[{"source_ref":"x1","source_id":"1","sentiment":"bullish","tags":["market","social","signal","risk","watch"],"summary":"summary","interpretation":"interpretation","confidence":"medium"}]}',
            model="fake",
            metadata={
                "parsed": {
                    "source": "x",
                    "posts": [
                        {
                            "source_ref": "x1",
                            "source_id": "1",
                            "sentiment": "bullish",
                            "tags": ["market", "social", "signal", "risk", "watch"],
                            "summary": "summary",
                            "interpretation": "interpretation",
                            "confidence": "medium",
                        }
                    ],
                }
            },
        )


class FakeRenderer:
    def __init__(self, title: str, calls: list[tuple[str, str, str]]) -> None:
        self.title = title
        self.calls = calls

    def render(self, response, *, mode: str, detail: str = "minimum") -> str:
        self.calls.append((self.title, mode, detail))
        return f"{self.title}:{mode}:{detail}"

    def render_trading(self, response, *, mode: str) -> str:
        return f"{self.title}:{mode}:trading"


class FakeRetentionService:
    def __init__(self) -> None:
        self.calls = 0

    async def prune(self, *, protected_paths=None, dry_run: bool = False) -> RetentionSummary:
        self.calls += 1
        return RetentionSummary(
            enabled=True,
            dry_run=dry_run,
            max_total_bytes=100,
            bytes_before=50,
            bytes_after=50,
        )




def _successful_collection_result() -> PipelineCollectionResult:
    return PipelineCollectionResult(profile="default", runs=[], warnings=[])


def _house_row(
    *,
    doc_id: str = "20024228",
    asset: str = "AAPL",
    asset_type_code: str | None = None,
    stock_ticker: str | None = None,
    transaction_date: str = "2026-06-20",
) -> StoredHousePtrTradeRow:
    if asset_type_code is None and asset.endswith("[ST]"):
        asset_type_code = "ST"
    if stock_ticker is None and asset_type_code == "ST":
        stock_ticker = "AAPL"
    return StoredHousePtrTradeRow(
        doc_id=doc_id,
        year=2026,
        name="Jane Doe",
        status="Member",
        state="CA",
        filing_date="2026-06-30",
        filing_date_utc="2026-06-30T00:00:00+00:00",
        pdf_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
        table_index=0,
        row_index=0,
        asset=asset,
        asset_type_code=asset_type_code,
        asset_type_label="Stocks, including ADRs" if asset_type_code == "ST" else None,
        stock_ticker=stock_ticker,
        transaction_type="Purchase",
        transaction_date=transaction_date,
        transaction_date_utc=f"{transaction_date}T00:00:00+00:00",
        transaction_action="purchase",
        amount="$1,001 - $15,000",
        raw_cells=["AAPL", "Purchase", "2026-06-20", "$1,001 - $15,000"],
        raw_metadata={},
        collected_at="2026-06-30T00:00:00+00:00",
    )


def _test_config(tmp_path):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(update={"artifact_dir": str(tmp_path / "jobs")}),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
        }
    )
