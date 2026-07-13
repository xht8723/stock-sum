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
        "/v1/social-reports/jobs",
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
        "/v1/social-reports/jobs/discord",
        json={},
    )

    assert create_response.status_code == 202
    assert manager.last_report_mode == "discord"
    assert manager.last_report_detail == "minimum"


def test_report_job_format_endpoint_allows_empty_body(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    create_response = client.post("/v1/social-reports/jobs/text")

    assert create_response.status_code == 202
    assert manager.last_report_mode == "text"


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


def test_trading_report_omitted_limit_uses_stock_sum_default(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/trading-reports/jobs/discord",
        json={"days": 30},
    )

    assert response.status_code == 202
    assert manager.last_trading_limit == 100


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


def test_trading_report_accepts_filing_date_filters(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/trading-reports/jobs/discord",
        json={"filing_start_date": "2026-07-01", "filing_end_date": "2026-07-08", "filing_days": None},
    )

    assert response.status_code == 202
    assert manager.last_trading_filing_start_date == "2026-07-01"
    assert manager.last_trading_filing_end_date == "2026-07-08"


def test_trading_report_accepts_filing_days(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post("/v1/trading-reports/jobs/discord", json={"filing_days": 1})

    assert response.status_code == 202
    assert manager.last_trading_filing_days == 1


def test_trading_report_accepts_allow_empty_without_changing_default(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    default_response = client.post("/v1/trading-reports/jobs/json", json={"filing_days": 1})
    assert default_response.status_code == 202
    assert manager.last_trading_allow_empty is False

    allowed_response = client.post(
        "/v1/trading-reports/jobs/json",
        json={"filing_days": 1, "allow_empty": True},
    )
    assert allowed_response.status_code == 202
    assert manager.last_trading_allow_empty is True


def test_13f_report_omitted_limit_uses_stock_sum_default(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/13f-reports/jobs/discord",
        json={"issuer": "NVIDIA"},
    )

    assert response.status_code == 202
    assert manager.last_13f_limit == 20


def test_13f_report_accepts_large_optional_limit(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/13f-reports/jobs/discord",
        json={"issuer": "NVIDIA", "limit": 5000},
    )

    assert response.status_code == 202
    assert manager.last_13f_limit == 5000


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


def test_statistic_fuzzy_matches_endpoint(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.get("/v1/statistics/fuzzy-matches", params={"mode": "social", "q": "Nvidia"})

    assert response.status_code == 200
    assert response.json()["matches"] == [
        {
            "mode": "social",
            "label": "nvidia",
            "row_count": 3,
            "statistic_filters": {"fuzzy_tag": "nvidia"},
        }
    ]
    assert manager.last_fuzzy_match == {"mode": "social", "query": "Nvidia", "limit": 5}


def test_statistic_job_rejects_missing_filters(tmp_path) -> None:
    config = _test_config(tmp_path)
    client = TestClient(create_app(config, job_manager=FakeJobManager(tmp_path)))

    response = client.post("/v1/statistics/jobs", json={"mode": "social"})

    assert response.status_code == 422
    assert "requires at least one filter" in response.json()["detail"]


def test_trendings_job_accepts_from_alias_and_limit(tmp_path) -> None:
    config = _test_config(tmp_path)
    manager = FakeJobManager(tmp_path)
    client = TestClient(create_app(config, job_manager=manager))

    response = client.post(
        "/v1/trendings/jobs/discord",
        json={"from": "2026-07-01", "to": "2026-07-06", "limit": 3},
    )

    assert response.status_code == 202
    assert response.json()["job_id"] == "job-trendings"
    assert manager.last_trendings_payload == {
        "mode": "discord",
        "from_date": "2026-07-01",
        "to_date": "2026-07-06",
        "limit": 3,
        "days": 1,
        "comparison_days": 7,
        "mentions_change_pct": 30.0,
        "sentiment_change_pct": 30.0,
        "minimum_mentions": 50,
    }


@dataclass
class FakeJob:
    job_id: str
    kind: str
    scope: str
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
        self.last_trading_filing_start_date: str | None = None
        self.last_trading_filing_end_date: str | None = None
        self.last_trading_filing_days: int | None = None
        self.last_trading_allow_empty: bool | None = None
        self.last_13f_limit: int | None = None
        self.last_statistic_mode: str | None = None
        self.last_statistic_ticker: str | None = None
        self.last_fuzzy_match: dict[str, Any] | None = None
        self.last_trendings_payload: dict[str, Any] | None = None

    def create_social_report_job(self, options) -> FakeJob:
        self.last_report_mode = options.mode
        self.last_report_detail = options.detail
        job = FakeJob(job_id="job-1", kind="social_report", scope="social")
        self.jobs[job.job_id] = job
        return job

    def create_trading_report_job(self, options) -> FakeJob:
        self.last_trading_limit = options.limit
        self.last_trading_asset_type = options.asset_type
        self.last_trading_ticker = options.ticker
        self.last_trading_filing_start_date = options.filing_start_date
        self.last_trading_filing_end_date = options.filing_end_date
        self.last_trading_filing_days = options.filing_days
        self.last_trading_allow_empty = options.allow_empty
        job = FakeJob(job_id="job-trading", kind="trading_report", scope="trading")
        self.jobs[job.job_id] = job
        return job

    def create_13f_report_job(self, options) -> FakeJob:
        self.last_13f_limit = options.limit
        job = FakeJob(job_id="job-13f", kind="13f_report", scope="sec_13f")
        self.jobs[job.job_id] = job
        return job

    def create_statistic_job(self, options) -> FakeJob:
        if not any((options.ticker, options.fuzzy_tag, options.name, options.asset_name, options.asset_type, options.days, options.start_date, options.end_date)):
            raise ValueError("Statistic requires at least one filter: ticker, fuzzy_tag, name, asset_name, asset_type, days, or date range.")
        self.last_statistic_mode = options.mode
        self.last_statistic_ticker = options.ticker
        job = FakeJob(job_id="job-statistic", kind="statistic", scope=options.mode)
        self.jobs[job.job_id] = job
        return job

    def create_trendings_report_job(self, options) -> FakeJob:
        self.last_trendings_payload = {
            "mode": options.mode,
            "from_date": options.from_date,
            "to_date": options.to_date,
            "limit": options.limit,
            "days": options.days,
            "comparison_days": options.comparison_days,
            "mentions_change_pct": options.mentions_change_pct,
            "sentiment_change_pct": options.sentiment_change_pct,
            "minimum_mentions": options.minimum_mentions,
        }
        job = FakeJob(job_id="job-trendings", kind="trendings_report", scope="trendings")
        self.jobs[job.job_id] = job
        return job

    async def statistic_fuzzy_matches(self, *, mode: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        self.last_fuzzy_match = {"mode": mode, "query": query, "limit": limit}
        return [{"mode": mode, "label": "nvidia", "row_count": 3, "statistic_filters": {"fuzzy_tag": "nvidia"}}]

    def create_collect_job(self) -> FakeJob:
        job = FakeJob(job_id="job-collect", kind="collect", scope="collect")
        self.jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> FakeJob | None:
        return self.jobs.get(job_id)

    async def run_social_report_job(self, job_id: str, options) -> None:
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

    async def run_13f_report_job(self, job_id: str, options) -> None:
        artifact = self.tmp_path / "13f-report.md"
        artifact.write_text("fake 13f report", encoding="utf-8")
        summary = self.tmp_path / "13f-summary.json"
        summary.write_text('{"summary":{"sec_13f":[]}}', encoding="utf-8")
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

    async def run_trendings_report_job(self, job_id: str, options) -> None:
        artifact = self.tmp_path / "trendings-report.md"
        artifact.write_text("fake trendings report", encoding="utf-8")
        summary = self.tmp_path / "trendings-summary.json"
        summary.write_text('{"report_type":"trendings"}', encoding="utf-8")
        job = self.jobs[job_id]
        job.status = "succeeded"
        job.phase = "succeeded"
        job.artifact_path = str(artifact)
        job.artifact_media_type = "text/markdown; charset=utf-8"
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
