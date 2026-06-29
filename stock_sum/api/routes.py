"""FastAPI route registration."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from stock_sum.api.jobs import HttpJobManager, ReportJobOptions
from stock_sum.config.loader import redacted_config
from stock_sum.config.models import AppConfig


class ReportJobRequest(BaseModel):
    """HTTP request body for a full report job."""

    mode: Literal["html", "markdown", "text", "json"] = "html"
    download_images: bool = False
    include_capitol_trades: bool = False
    capitol_trades_limit: int = Field(default=12, ge=1)
    instructions: str | None = None
    title: str = "Market Social Digest"
    max_images_per_post: int = Field(default=3, ge=0)
    max_images_total: int = Field(default=20, ge=0)


def build_router(config: AppConfig | None = None, *, job_manager: HttpJobManager | None = None) -> APIRouter:
    """Build API routes."""

    router = APIRouter()
    manager = job_manager or (HttpJobManager(config) if config is not None else None)

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/reports/{profile}/run", status_code=202)
    async def run_report(profile: str) -> dict[str, str]:
        if config is not None and profile not in config.reports:
            raise HTTPException(status_code=404, detail=f"Unknown report profile: {profile}")
        return {"status": "accepted", "profile": profile}

    @router.get("/config/effective")
    async def effective_config() -> dict:
        if config is None:
            return {}
        return redacted_config(config)

    v1 = APIRouter(prefix="/v1", dependencies=[Depends(_reject_blacklisted_ip(config))])

    @v1.get("/config/effective")
    async def v1_effective_config() -> dict:
        if config is None:
            return {}
        return redacted_config(config)

    @v1.post("/reports/{profile}/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_report_job(
        profile: str,
        request: ReportJobRequest,
        background_tasks: BackgroundTasks,
    ) -> dict:
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        try:
            options = ReportJobOptions(**request.model_dump())
            job = manager.create_report_job(profile, options)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        background_tasks.add_task(manager.run_report_job, job.job_id, options)
        return _job_response(job.to_dict())

    @v1.post("/collect/{profile}/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_collect_job(profile: str, background_tasks: BackgroundTasks) -> dict:
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
        if manager is None:
            raise HTTPException(status_code=503, detail="HTTP job manager is not configured.")
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return _job_response(job.to_dict())

    @v1.get("/jobs/{job_id}/summary")
    async def get_job_summary(job_id: str) -> dict:
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

    router.include_router(v1)
    return router


def _reject_blacklisted_ip(config: AppConfig | None):
    async def dependency(request: Request) -> None:
        if config is None:
            return
        client_ip = request.client.host if request.client is not None else None
        if client_ip in set(config.server.blacklisted_ips):
            raise HTTPException(status_code=403, detail=f"Client IP is blacklisted: {client_ip}")

    return dependency


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


def _read_job_json(path: str) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
