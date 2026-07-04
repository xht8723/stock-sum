"""FastAPI route registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from stock_sum.api.jobs import HttpJobManager, ReportJobOptions, Sec13FReportJobOptions, StatisticJobOptions, TradingReportJobOptions
from stock_sum.api.runtime_config import RuntimeConfigError, RuntimeConfigManager
from stock_sum.config.loader import redacted_config
from stock_sum.config.models import AppConfig
from stock_sum.config.writer import (
    add_profile,
    add_subreddit,
    add_x_user,
    delete_profile,
    delete_subreddit,
    delete_x_user,
    edit_profile,
    set_house_ptr_source,
)
from stock_sum.llm.registry import get_llm_provider, list_llm_providers
from stock_sum.retention import DataRetentionService


ReportModePath = Literal["html", "markdown", "discord", "text", "json"]
SocialReportDetailPath = Literal["minimum", "medium", "full"]
StatisticModePath = Literal["social", "trading"]
StatisticBucketPath = Literal["auto", "day", "week", "month"]
StatisticSourcePath = Literal["x", "reddit", "all"]
StatisticSentimentPath = Literal["bullish", "bearish", "mixed", "neutral", "unclear", "all"]
StatisticActionPath = Literal["purchase", "sell", "sell_partial", "all"]


class ReportJobRequest(BaseModel):
    """HTTP request body for a full report job."""

    mode: ReportModePath = "html"
    detail: SocialReportDetailPath = "minimum"
    download_images: bool = False
    instructions: str | None = None
    title: str = "Market Social Digest"
    max_images_per_post: int = Field(default=3, ge=0)
    max_images_total: int = Field(default=20, ge=0)


class ProfileRequest(BaseModel):
    """Request body for creating a report profile."""

    name: str
    collector_ids: list[str] = Field(default_factory=list)
    overwrite: bool = False


class ProfilePatchRequest(BaseModel):
    """Request body for editing a report profile."""

    collector_ids: list[str] | None = None


class XUserRequest(BaseModel):
    """Request body for adding or replacing an X user source."""

    handle: str
    enabled: bool = True
    limit: int = Field(default=100, ge=1)
    lookback_hours: int = Field(default=24, ge=1)
    profile: str | None = None
    overwrite: bool = True


class SubredditRequest(BaseModel):
    """Request body for adding or replacing a subreddit source."""

    subreddit: str
    enabled: bool = True
    sort: str = "new"
    timeframe: str = "day"
    limit: int = Field(default=100, ge=1)
    lookback_hours: int = Field(default=24, ge=1)
    trim: bool = True
    include_comments: bool = True
    comments_per_post: int = Field(default=10, ge=0)
    profile: str | None = None
    overwrite: bool = True


class HousePtrRequest(BaseModel):
    """Request body for updating House PTR source settings."""

    enabled: bool = True
    year: int | None = Field(default=None, ge=0)
    refresh_ttl_seconds: int = Field(default=21600, ge=0)
    download_concurrency: int = Field(default=4, ge=1)
    parse_concurrency: int = Field(default=2, ge=1)
    zip_url_template: str = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
    pdf_url_template: str = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf"
    profile: str | None = "default"


class TradingReportJobRequest(BaseModel):
    """HTTP request body for a House PTR trading disclosure report."""

    mode: ReportModePath = "html"
    name: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = Field(default=None, ge=1)
    asset_type: str | None = None
    ticker: str | None = None
    limit: int | None = Field(default=None, ge=1)
    title: str = "Official Trading Disclosures"
    force_refresh: bool = False


class Sec13FReportJobRequest(BaseModel):
    """HTTP request body for an SEC 13F holdings report."""

    mode: ReportModePath = "html"
    manager: str | None = None
    cik: str | None = None
    accession_number: str | None = None
    issuer: str | None = None
    cusip: str | None = None
    figi: str | None = None
    put_call: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    filing_start: str | None = None
    filing_end: str | None = None
    min_value: int | None = Field(default=None, ge=0)
    min_shares: int | None = Field(default=None, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    title: str = "SEC 13F Holdings"
    force_refresh: bool = False


class StatisticJobRequest(BaseModel):
    """HTTP request body for a statistic PNG job."""

    mode: StatisticModePath = "social"
    profile: str = "default"
    ticker: str | None = None
    name: str | None = None
    asset_type: str | None = None
    action: StatisticActionPath = "all"
    source: StatisticSourcePath = "all"
    sentiment: StatisticSentimentPath = "all"
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = Field(default=None, ge=1)
    bucket: StatisticBucketPath = "auto"
    title: str = "Stock-Sum Statistic"


class LLMConfigPatchRequest(BaseModel):
    """Request body for selecting an LLM provider/model."""

    provider: str | None = None
    model: str | None = None


class SecretRequest(BaseModel):
    """Write-only secret value request."""

    value: str = Field(min_length=1)


class RetentionPruneRequest(BaseModel):
    """Request body for retention pruning."""

    dry_run: bool = True


def build_router(
    config: AppConfig | None = None,
    *,
    job_manager: HttpJobManager | None = None,
    runtime_config: RuntimeConfigManager | None = None,
) -> APIRouter:
    """Build API routes."""

    router = APIRouter()
    runtime = runtime_config or (RuntimeConfigManager(config) if config is not None else None)
    manager_holder: dict[str, Any] = {
        "version": runtime.version if runtime is not None else 0,
        "manager": job_manager or (HttpJobManager(runtime.config) if runtime is not None else None),
        "stale_recovery_done": job_manager is not None or runtime is not None,
    }

    def current_config() -> AppConfig | None:
        return runtime.config if runtime is not None else config

    def current_manager() -> HttpJobManager | None:
        if job_manager is not None:
            return job_manager
        if runtime is None:
            return None
        if manager_holder["manager"] is None or manager_holder["version"] != runtime.version:
            manager_holder["manager"] = HttpJobManager(
                runtime.config,
                recover_stale_jobs=not manager_holder["stale_recovery_done"],
            )
            manager_holder["stale_recovery_done"] = True
            manager_holder["version"] = runtime.version
        return manager_holder["manager"]

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/config/effective")
    async def effective_config() -> dict:
        active = current_config()
        if active is None:
            return {}
        return redacted_config(active)

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(_reject_blacklisted_ip(current_config))])
    management_dependencies = [Depends(_require_local_management(current_config))]

    @v1.get("/config/effective")
    async def v1_effective_config() -> dict:
        active = current_config()
        if active is None:
            return {}
        return redacted_config(active)

    @v1.post("/social-reports/{profile}/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_social_report_job(
        profile: str,
        background_tasks: BackgroundTasks,
        request: ReportJobRequest = ReportJobRequest(),
    ) -> dict:
        return _create_social_report_job(current_manager(), profile, request, background_tasks)

    @v1.post("/social-reports/{profile}/jobs/{mode}", status_code=status.HTTP_202_ACCEPTED)
    async def create_social_report_job_for_mode(
        profile: str,
        mode: ReportModePath,
        background_tasks: BackgroundTasks,
        request: ReportJobRequest = ReportJobRequest(),
    ) -> dict:
        return _create_social_report_job(current_manager(), profile, request, background_tasks, mode=mode)

    @v1.post("/trading-reports/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_trading_report_job(
        background_tasks: BackgroundTasks,
        request: TradingReportJobRequest = TradingReportJobRequest(),
    ) -> dict:
        return _create_trading_report_job(current_manager(), request, background_tasks)

    @v1.post("/trading-reports/jobs/{mode}", status_code=status.HTTP_202_ACCEPTED)
    async def create_trading_report_job_for_mode(
        mode: ReportModePath,
        background_tasks: BackgroundTasks,
        request: TradingReportJobRequest = TradingReportJobRequest(),
    ) -> dict:
        data = request.model_dump()
        data["mode"] = mode
        return _create_trading_report_job(current_manager(), TradingReportJobRequest(**data), background_tasks)

    @v1.post("/13f-reports/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_13f_report_job(
        background_tasks: BackgroundTasks,
        request: Sec13FReportJobRequest = Sec13FReportJobRequest(),
    ) -> dict:
        return _create_13f_report_job(current_manager(), request, background_tasks)

    @v1.post("/13f-reports/jobs/{mode}", status_code=status.HTTP_202_ACCEPTED)
    async def create_13f_report_job_for_mode(
        mode: ReportModePath,
        background_tasks: BackgroundTasks,
        request: Sec13FReportJobRequest = Sec13FReportJobRequest(),
    ) -> dict:
        data = request.model_dump()
        data["mode"] = mode
        return _create_13f_report_job(current_manager(), Sec13FReportJobRequest(**data), background_tasks)

    @v1.post("/statistics/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_statistic_job(
        background_tasks: BackgroundTasks,
        request: StatisticJobRequest = StatisticJobRequest(),
    ) -> dict:
        return _create_statistic_job(current_manager(), request, background_tasks)

    def _create_social_report_job(
        manager: HttpJobManager | None,
        profile: str,
        request: ReportJobRequest,
        background_tasks: BackgroundTasks,
        *,
        mode: ReportModePath | None = None,
    ) -> dict:
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            data = request.model_dump()
            if mode is not None:
                data["mode"] = mode
            options = ReportJobOptions(**data)
            job = manager.create_report_job(profile, options)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_report_job, job.job_id, options)
        return _job_response(job.to_dict())

    def _create_trading_report_job(
        manager: HttpJobManager | None,
        request: TradingReportJobRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            options = TradingReportJobOptions(**request.model_dump())
            job = manager.create_trading_report_job(options)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_trading_report_job, job.job_id, options)
        return _job_response(job.to_dict())

    def _create_13f_report_job(
        manager: HttpJobManager | None,
        request: Sec13FReportJobRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            options = Sec13FReportJobOptions(**request.model_dump())
            job = manager.create_13f_report_job(options)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_13f_report_job, job.job_id, options)
        return _job_response(job.to_dict())

    def _create_statistic_job(
        manager: HttpJobManager | None,
        request: StatisticJobRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            options = StatisticJobOptions(**request.model_dump())
            job = manager.create_statistic_job(options)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_statistic_job, job.job_id, options)
        return _job_response(job.to_dict())

    @v1.post("/collect/{profile}/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_collect_job(profile: str, background_tasks: BackgroundTasks) -> dict:
        manager = current_manager()
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            job = manager.create_collect_job(profile)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_collect_job, job.job_id)
        return _job_response(job.to_dict())

    @v1.get("/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        manager = current_manager()
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return _job_response(job.to_dict())

    @v1.get("/jobs/{job_id}/summary")
    async def get_job_summary(job_id: str) -> dict:
        manager = current_manager()
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        if job.status != "succeeded" or not job.summary_path:
            raise HTTPException(status_code=404, detail="Job summary is not available.")
        return _read_job_json(job.summary_path)

    @v1.get("/jobs/{job_id}/artifact")
    async def get_job_artifact(job_id: str) -> FileResponse:
        manager = current_manager()
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        if job.status != "succeeded" or not job.artifact_path:
            raise HTTPException(status_code=404, detail="Job artifact is not available.")
        artifact_path = Path(job.artifact_path)
        if not artifact_path.exists():
            raise HTTPException(status_code=404, detail="Job artifact file is missing.")
        return FileResponse(
            artifact_path,
            media_type=job.artifact_media_type or "application/octet-stream",
            filename=artifact_path.name,
        )

    @v1.get("/profiles", dependencies=management_dependencies)
    async def list_report_profiles() -> dict[str, Any]:
        active = _require_config(current_config())
        return {
            "profiles": [
                {"name": name, "profile": profile.model_dump(mode="json")}
                for name, profile in sorted(active.reports.items())
            ]
        }

    @v1.get("/profiles/{name}", dependencies=management_dependencies)
    async def get_report_profile(name: str) -> dict[str, Any]:
        active = _require_config(current_config())
        try:
            profile = active.reports[name]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Profile does not exist: {name}") from exc
        return {"name": name, "profile": profile.model_dump(mode="json")}

    @v1.post("/profiles", dependencies=management_dependencies)
    async def create_profile(request: ProfileRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        _mutate_runtime(
            runtime_manager,
            lambda path: add_profile(
                path,
                request.name,
                collector_ids=request.collector_ids,
                overwrite=request.overwrite,
            ),
        )
        return {"name": request.name, "profile": runtime_manager.config.reports[request.name].model_dump(mode="json")}

    @v1.patch("/profiles/{name}", dependencies=management_dependencies)
    async def patch_profile(name: str, request: ProfilePatchRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        _mutate_runtime(
            runtime_manager,
            lambda path: edit_profile(
                path,
                name,
                collector_ids=request.collector_ids,
            ),
        )
        return {"name": name, "profile": runtime_manager.config.reports[name].model_dump(mode="json")}

    @v1.delete("/profiles/{name}", dependencies=management_dependencies)
    async def remove_profile(name: str) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        _mutate_runtime(runtime_manager, lambda path: delete_profile(path, name))
        return {"deleted": name}

    @v1.get("/sources", dependencies=management_dependencies)
    async def list_sources() -> dict[str, Any]:
        active = _require_config(current_config())
        return {
            "x_users": [source.model_dump(mode="json") for source in active.sources.x_users],
            "subreddits": [source.model_dump(mode="json") for source in active.sources.subreddits],
            "house_ptr": active.sources.house_ptr.model_dump(mode="json"),
        }

    @v1.get("/sources/x-users", dependencies=management_dependencies)
    async def list_x_sources() -> dict[str, Any]:
        active = _require_config(current_config())
        return {"x_users": [source.model_dump(mode="json") for source in active.sources.x_users]}

    @v1.post("/sources/x-users", dependencies=management_dependencies)
    async def create_x_source(request: XUserRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        collector_id = _mutate_runtime(
            runtime_manager,
            lambda path: add_x_user(
                path,
                request.handle,
                enabled=request.enabled,
                limit=request.limit,
                lookback_hours=request.lookback_hours,
                profile=request.profile,
                overwrite=request.overwrite,
            ),
        )
        return {"collector_id": collector_id, "sources": [item.model_dump(mode="json") for item in runtime_manager.config.sources.x_users]}

    @v1.delete("/sources/x-users/{handle}", dependencies=management_dependencies)
    async def remove_x_source(handle: str, profile: str | None = None) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        collector_id = _mutate_runtime(runtime_manager, lambda path: delete_x_user(path, handle, profile=profile))
        return {"deleted": collector_id}

    @v1.get("/sources/subreddits", dependencies=management_dependencies)
    async def list_reddit_sources() -> dict[str, Any]:
        active = _require_config(current_config())
        return {"subreddits": [source.model_dump(mode="json") for source in active.sources.subreddits]}

    @v1.post("/sources/subreddits", dependencies=management_dependencies)
    async def create_reddit_source(request: SubredditRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        collector_id = _mutate_runtime(
            runtime_manager,
            lambda path: add_subreddit(
                path,
                request.subreddit,
                enabled=request.enabled,
                sort=request.sort,
                timeframe=request.timeframe,
                limit=request.limit,
                lookback_hours=request.lookback_hours,
                trim=request.trim,
                include_comments=request.include_comments,
                comments_per_post=request.comments_per_post,
                profile=request.profile,
                overwrite=request.overwrite,
            ),
        )
        return {"collector_id": collector_id, "sources": [item.model_dump(mode="json") for item in runtime_manager.config.sources.subreddits]}

    @v1.delete("/sources/subreddits/{subreddit}", dependencies=management_dependencies)
    async def remove_reddit_source(subreddit: str, profile: str | None = None) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        collector_id = _mutate_runtime(runtime_manager, lambda path: delete_subreddit(path, subreddit, profile=profile))
        return {"deleted": collector_id}

    @v1.get("/sources/house-ptr", dependencies=management_dependencies)
    async def get_house_ptr_source() -> dict[str, Any]:
        active = _require_config(current_config())
        return {"house_ptr": active.sources.house_ptr.model_dump(mode="json")}

    @v1.patch("/sources/house-ptr", dependencies=management_dependencies)
    async def patch_house_ptr_source(request: HousePtrRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        collector_id = _mutate_runtime(
            runtime_manager,
            lambda path: set_house_ptr_source(
                path,
                enabled=request.enabled,
                year=request.year,
                refresh_ttl_seconds=request.refresh_ttl_seconds,
                download_concurrency=request.download_concurrency,
                parse_concurrency=request.parse_concurrency,
                zip_url_template=request.zip_url_template,
                pdf_url_template=request.pdf_url_template,
                profile=request.profile,
            ),
        )
        return {"collector_id": collector_id, "house_ptr": runtime_manager.config.sources.house_ptr.model_dump(mode="json")}

    @v1.get("/llm/providers", dependencies=management_dependencies)
    async def llm_providers() -> dict[str, Any]:
        return {"providers": [provider.__dict__ for provider in list_llm_providers()]}

    @v1.get("/llm/config", dependencies=management_dependencies)
    async def llm_config() -> dict[str, Any]:
        active = _require_config(current_config())
        return {"llm": active.llm.model_dump(mode="json")}

    @v1.patch("/llm/config", dependencies=management_dependencies)
    async def patch_llm_config(request: LLMConfigPatchRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)

        def mutate(path: Path) -> None:
            from stock_sum.config.writer import set_dotted_value

            if request.provider is not None:
                descriptor = get_llm_provider(request.provider)
                if not descriptor.implemented:
                    raise ValueError(f"LLM provider is not implemented: {request.provider}")
                set_dotted_value(path, "llm.provider", descriptor.provider_id)
                set_dotted_value(path, "llm.model", request.model or descriptor.default_model)
                set_dotted_value(path, "llm.api_key_env", descriptor.api_key_env)
                set_dotted_value(path, "llm.base_url", descriptor.base_url)
            elif request.model is not None:
                set_dotted_value(path, "llm.model", request.model)

        _mutate_runtime(runtime_manager, mutate)
        return {"llm": runtime_manager.config.llm.model_dump(mode="json")}

    @v1.get("/secrets", dependencies=management_dependencies)
    async def list_secrets() -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        return {"env_file": str(runtime_manager.env_file), "secrets": runtime_manager.secret_names()}

    @v1.put("/secrets/{name}", dependencies=management_dependencies)
    async def put_secret(name: str, request: SecretRequest) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        try:
            runtime_manager.set_secret_value(name, request.value)
        except (RuntimeConfigError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime_manager.reload()
        return {"name": name, "set": True}

    @v1.delete("/secrets/{name}", dependencies=management_dependencies)
    async def delete_secret(name: str) -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        try:
            removed = runtime_manager.remove_secret_value(name)
        except (RuntimeConfigError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        runtime_manager.reload()
        return {"name": name, "removed": removed}

    @v1.get("/setup/check", dependencies=management_dependencies)
    async def setup_check() -> dict[str, Any]:
        runtime_manager = _require_runtime(runtime)
        issues = runtime_manager.setup_issues()
        return {"status": "ok" if not issues else "issues", "issues": issues}

    @v1.get("/retention/status", dependencies=management_dependencies)
    async def retention_status() -> dict[str, Any]:
        active = _require_config(current_config())
        data = (await DataRetentionService(active).status()).to_dict()
        return _with_memory_status(data, current_manager())

    @v1.post("/retention/prune", dependencies=management_dependencies)
    async def retention_prune(request: RetentionPruneRequest) -> dict[str, Any]:
        active = _require_config(current_config())
        data = (await DataRetentionService(active).prune(dry_run=request.dry_run)).to_dict()
        manager = current_manager()
        if manager is not None:
            evicted = manager._refresh_memory_status()
            return _with_memory_status(data, manager, evicted_in_memory_jobs=evicted)
        return data

    router.include_router(v1)
    return router


def _reject_blacklisted_ip(config_getter: Callable[[], AppConfig | None]):
    async def dependency(request: Request) -> None:
        config = config_getter()
        if config is None:
            return
        client_ip = request.client.host if request.client is not None else None
        if client_ip in set(config.server.blacklisted_ips):
            raise HTTPException(status_code=403, detail=f"Client IP is blacklisted: {client_ip}")

    return dependency


def _require_local_management(config_getter: Callable[[], AppConfig | None]):
    async def dependency(request: Request) -> None:
        config = config_getter()
        if config is None or config.server.management_allow_remote:
            return
        client_ip = request.client.host if request.client is not None else None
        if client_ip in {"127.0.0.1", "::1", "localhost", "testclient"}:
            return
        raise HTTPException(status_code=403, detail=f"Management API requires a loopback client: {client_ip}")

    return dependency


def _require_config(config: AppConfig | None) -> AppConfig:
    if config is None:
        raise HTTPException(status_code=503, detail="Runtime config is not configured.")
    return config


def _require_runtime(runtime: RuntimeConfigManager | None) -> RuntimeConfigManager:
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime config manager is not configured.")
    return runtime


def _mutate_runtime(runtime: RuntimeConfigManager, callback):
    try:
        return runtime.mutate_config(callback)
    except RuntimeConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _job_response(data: dict) -> dict:
    job_id = data["job_id"]
    data = dict(data)
    data["status_url"] = f"/v1/jobs/{job_id}"
    if data.get("artifact_path"):
        data["artifact_url"] = f"/v1/jobs/{job_id}/artifact"
    if data.get("summary_path"):
        data["summary_url"] = f"/v1/jobs/{job_id}/summary"
    data.pop("artifact_path", None)
    data.pop("summary_path", None)
    return data


def _with_memory_status(
    data: dict[str, Any],
    manager: HttpJobManager | None,
    *,
    evicted_in_memory_jobs: int | None = None,
) -> dict[str, Any]:
    if manager is None:
        return data
    result = dict(data)
    result.update(manager.memory_status(evicted_in_memory_jobs=evicted_in_memory_jobs))
    return result


def _read_job_json(path: str) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
