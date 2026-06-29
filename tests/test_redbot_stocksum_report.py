"""Tests for the Redbot stock-sum report cog HTTP bridge."""

from __future__ import annotations

from typing import Any

import pytest

from redbot_cogs.stocksum_report.stocksum_report import (
    StockSumConfigurationError,
    StockSumHttpClient,
    StockSumRequestError,
)


async def test_client_sends_report_request_and_downloads_artifact() -> None:
    session = FakeSession(
        post_responses=[
            FakeResponse(202, {"job_id": "job-1"}),
        ],
        get_responses=[
            FakeResponse(200, {"job_id": "job-1", "status": "queued"}),
            FakeResponse(200, {"job_id": "job-1", "status": "succeeded"}),
            FakeResponse(
                200,
                body=b"<html>ok</html>",
                headers={
                    "content-type": "text/html; charset=utf-8",
                    "content-disposition": 'attachment; filename="report.html"',
                },
            ),
        ],
    )
    client = StockSumHttpClient(
        base_url="http://stock-sum.local",
        token="secret-token",
        session=session,
        poll_seconds=0,
    )

    artifact = await client.run_report(
        profile="default",
        output_format="html",
        include_capitol_trades=True,
    )

    assert artifact.job_id == "job-1"
    assert artifact.filename == "report.html"
    assert artifact.content == b"<html>ok</html>"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/reports/default/jobs",
        {
            "headers": {"Authorization": "Bearer secret-token"},
            "json": {"mode": "html", "include_capitol_trades": True},
        },
    )
    assert session.requests[1][2]["headers"] == {"Authorization": "Bearer secret-token"}


async def test_client_reports_failed_job() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-2"})],
        get_responses=[FakeResponse(200, {"job_id": "job-2", "status": "failed", "error": "LLM failed"})],
    )
    client = StockSumHttpClient(token="secret-token", session=session, poll_seconds=0)

    with pytest.raises(StockSumRequestError, match="LLM failed"):
        await client.run_report(profile="default", output_format="html", include_capitol_trades=False)


async def test_client_reports_timeout() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-3"})],
        get_responses=[],
    )
    client = StockSumHttpClient(
        token="secret-token",
        session=session,
        poll_seconds=0,
        timeout_seconds=0,
    )

    with pytest.raises(StockSumRequestError, match="timed out"):
        await client.run_report(profile="default", output_format="html", include_capitol_trades=False)


def test_client_requires_local_http_token() -> None:
    with pytest.raises(StockSumConfigurationError, match="STOCK_SUM_HTTP_TOKEN"):
        StockSumHttpClient(token="")


async def test_client_maps_auth_failure() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(401, {"detail": "Invalid or missing bearer token."})],
        get_responses=[],
    )
    client = StockSumHttpClient(token="bad-token", session=session)

    with pytest.raises(StockSumRequestError, match="rejected"):
        await client.run_report(profile="default", output_format="html", include_capitol_trades=False)


class FakeSession:
    def __init__(self, *, post_responses: list[FakeResponse], get_responses: list[FakeResponse]) -> None:
        self.post_responses = post_responses
        self.get_responses = get_responses
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("POST", url, kwargs))
        return FakeContext(self.post_responses.pop(0))

    def get(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("GET", url, kwargs))
        return FakeContext(self.get_responses.pop(0))

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, response: "FakeResponse") -> None:
        self.response = response

    async def __aenter__(self) -> "FakeResponse":
        return self.response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class FakeResponse:
    def __init__(
        self,
        status: int,
        json_payload: Any | None = None,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.json_payload = json_payload
        self.body = body
        self.headers = headers or {}

    async def json(self) -> Any:
        if self.json_payload is None:
            raise ValueError("no json")
        return self.json_payload

    async def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    async def read(self) -> bytes:
        return self.body
