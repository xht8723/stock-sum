"""HTTP API smoke tests."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from stock_sum.api.app import create_app
from stock_sum.config.loader import load_config


def test_health_route() -> None:
    client = TestClient(create_app())
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_v1_config_returns_redacted_config_without_auth(tmp_path) -> None:
    config = _test_config(tmp_path)
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))

    response = client.get("/v1/config/effective")

    assert response.status_code == 200
    assert response.json()["server"]["blacklisted_ips"] == []


def test_v1_rejects_blacklisted_ip(tmp_path) -> None:
    config = _test_config(tmp_path, blacklisted_ips=["testclient"])
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))

    response = client.get("/v1/config/effective")

    assert response.status_code == 403
    assert "blacklisted" in response.json()["detail"]


def test_report_job_lifecycle_and_artifact_download(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    create_response = client.post(
        "/v1/social-reports/default/jobs",
        json={"mode": "html", "detail": "medium"},
    )

    assert create_response.status_code == 202
    assert manager.last_report_mode == "html"
    assert manager.last_report_detail == "medium"
    job_id = create_response.json()["job_id"]

    status_response = client.get(f"/v1/jobs/{job_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "succeeded"
    assert status_response.json()["artifact_url"] == f"/v1/jobs/{job_id}/artifact"

    artifact_response = client.get(f"/v1/jobs/{job_id}/artifact")
    assert artifact_response.status_code == 200
    assert "fake report" in artifact_response.text


def test_report_job_format_endpoint_sets_mode(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    create_response = client.post(
        "/v1/social-reports/default/jobs/discord",
        json={},
    )

    assert create_response.status_code == 202
    assert manager.last_report_mode == "discord"
    assert manager.last_report_detail == "minimum"


def test_report_job_format_endpoint_allows_empty_body(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    create_response = client.post("/v1/social-reports/default/jobs/text")

    assert create_response.status_code == 202
    assert manager.last_report_mode == "text"


def test_missing_profile_returns_404(tmp_path) -> None:
    config = _test_config(tmp_path)
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))

    response = client.post(
        "/v1/social-reports/missing/jobs",
        json={"mode": "html"},
    )

    assert response.status_code == 404


def test_removed_report_compatibility_routes_return_404(tmp_path) -> None:
    config = _test_config(tmp_path)
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))
    top_level_route = "/" + "reports" + "/default/run"
    v1_base_route = "/v1/" + "reports" + "/default/jobs"

    assert client.post(top_level_route).status_code == 404
    assert client.post(v1_base_route).status_code == 404
    assert client.post(f"{v1_base_route}/discord").status_code == 404


def test_trading_report_accepts_large_optional_limit(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/trading-reports/jobs/discord",
        json={"days": 30, "limit": 500},
    )

    assert response.status_code == 202
    assert manager.last_trading_limit == 500


def test_trading_report_accepts_asset_type_and_ticker_filters(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/trading-reports/jobs/discord",
        json={"asset_type": "st", "ticker": "amzn"},
    )

    assert response.status_code == 202
    assert manager.last_trading_asset_type == "st"
    assert manager.last_trading_ticker == "amzn"


def test_statistic_job_accepts_filters(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/statistics/jobs",
        json={"mode": "social", "ticker": "NVDA", "days": 30, "bucket": "auto"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-statistic"
    assert manager.last_statistic_mode == "social"
    assert manager.last_statistic_ticker == "NVDA"


def test_statistic_job_rejects_missing_filters(tmp_path) -> None:
    config = _test_config(tmp_path)
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))

    response = client.post("/v1/statistics/jobs", json={"mode": "social"})

    assert response.status_code == 422
    assert "requires at least one filter" in response.json()["detail"]


@dataclass
class FakeJob:
    job_id: str
    kind: str
    profile: str
    status: str = "queued"
    phase: str = "queued"
    created_at: str = "2026-06-28T00:00:00+00:00"
    updated_at: str = "2026-06-28T00:00:00+00:00"
    artifact_path: str | None = None
    artifact_media_type: str | None = None
    summary_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FakeJobManager:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.jobs: dict[str, FakeJob] = {}
        self.last_report_mode: str | None = None
        self.last_report_detail: str | None = None
        self.last_trading_limit: int | None = None
        self.last_trading_asset_type: str | None = None
        self.last_trading_ticker: str | None = None
        self.last_statistic_mode: str | None = None
        self.last_statistic_ticker: str | None = None

    def create_report_job(self, profile: str, options) -> FakeJob:
        if profile != "default":
            raise KeyError(f"Unknown report profile: {profile}")
        self.last_report_mode = options.mode
        self.last_report_detail = options.detail
        job = FakeJob(job_id="job-1", kind="report", profile=profile)
        self.jobs[job.job_id] = job
        return job

    def create_trading_report_job(self, options) -> FakeJob:
        self.last_trading_limit = options.limit
        self.last_trading_asset_type = options.asset_type
        self.last_trading_ticker = options.ticker
        job = FakeJob(job_id="job-trading", kind="trading_report", profile="trading")
        self.jobs[job.job_id] = job
        return job

    def create_statistic_job(self, options) -> FakeJob:
        if not any((options.ticker, options.name, options.asset_type, options.days, options.start_date, options.end_date)):
            raise ValueError("Statistic requires at least one filter: ticker, name, asset_type, days, or date range.")
        self.last_statistic_mode = options.mode
        self.last_statistic_ticker = options.ticker
        job = FakeJob(job_id="job-statistic", kind="statistic", profile=options.profile)
        self.jobs[job.job_id] = job
        return job

    def create_collect_job(self, profile: str) -> FakeJob:
        if profile != "default":
            raise KeyError(f"Unknown report profile: {profile}")
        job = FakeJob(job_id="job-collect", kind="collect", profile=profile)
        self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> FakeJob | None:
        return self.jobs.get(job_id)

    async def run_report_job(self, job_id: str, options) -> None:
        artifact = self.tmp_path / "report.html"
        artifact.write_text("<html>fake report</html>", encoding="utf-8")
        summary = self.tmp_path / "summary.json"
        summary.write_text('{"summary":{"ok":true}}', encoding="utf-8")
        job = self.jobs[job_id]
        job.status = "succeeded"
        job.phase = "succeeded"
        job.artifact_path = str(artifact)
        job.artifact_media_type = "text/html; charset=utf-8"
        job.summary_path = str(summary)

    async def run_trading_report_job(self, job_id: str, options) -> None:
        artifact = self.tmp_path / "trading-report.md"
        artifact.write_text("fake trading report", encoding="utf-8")
        summary = self.tmp_path / "trading-summary.json"
        summary.write_text('{"summary":{"house_ptr":[]}}', encoding="utf-8")
        job = self.jobs[job_id]
        job.status = "succeeded"
        job.phase = "succeeded"
        job.artifact_path = str(artifact)
        job.artifact_media_type = "text/markdown; charset=utf-8"
        job.summary_path = str(summary)

    async def run_statistic_job(self, job_id: str, options) -> None:
        artifact = self.tmp_path / "statistic.png"
        artifact.write_bytes(b"png")
        summary = self.tmp_path / "statistic-summary.json"
        summary.write_text('{"report_type":"statistic"}', encoding="utf-8")
        job = self.jobs[job_id]
        job.status = "succeeded"
        job.phase = "succeeded"
        job.artifact_path = str(artifact)
        job.artifact_media_type = "image/png"
        job.summary_path = str(summary)

    async def run_collect_job(self, job_id: str) -> None:
        artifact = self.tmp_path / "collection.json"
        artifact.write_text('{"collected_count":0}', encoding="utf-8")
        job = self.jobs[job_id]
        job.status = "succeeded"
        job.phase = "succeeded"
        job.artifact_path = str(artifact)
        job.artifact_media_type = "application/json"


def _test_config(tmp_path: Path, *, blacklisted_ips: list[str] | None = None):
    config = load_config(Path("stock_sum/config/example.toml"))
    return config.model_copy(
        update={
            "server": config.server.model_copy(
                update={
                    "artifact_dir": str(tmp_path / "jobs"),
                    "blacklisted_ips": blacklisted_ips or [],
                }
            ),
            "storage": config.storage.model_copy(update={"sqlite_path": str(tmp_path / "stock_sum.sqlite3")}),
        }
    )
