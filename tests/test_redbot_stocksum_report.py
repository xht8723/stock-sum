"""Tests for the Redbot stock-sum report cog HTTP bridge."""

from __future__ import annotations

from typing import Any

import pytest

from redbot_cogs.stocksum_report.stocksum_report import (
    StockSumArtifact,
    StockSumReport,
    StockSumHttpClient,
    StockSumRequestError,
    _failure_message,
    _split_discord_markdown,
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
        session=session,
        poll_seconds=0,
    )

    artifact = await client.run_report(
        profile="default",
        output_format="html",
    )

    assert artifact.job_id == "job-1"
    assert artifact.filename == "report.html"
    assert artifact.content == b"<html>ok</html>"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/reports/default/jobs/html",
        {
            "headers": {},
            "json": {},
        },
    )
    assert session.requests[1][2]["headers"] == {}


async def test_client_uses_discord_format_endpoint() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-discord"})],
        get_responses=[
            FakeResponse(200, {"job_id": "job-discord", "status": "succeeded"}),
            FakeResponse(
                200,
                body=b"**Market Social Digest**",
                headers={"content-type": "text/markdown; charset=utf-8"},
            ),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    artifact = await client.run_report(
        profile="default",
        output_format="discord",
    )

    assert artifact.filename == "stock-sum-report-job-discord.md"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/reports/default/jobs/discord",
        {
            "headers": {},
            "json": {},
        },
    )


async def test_client_reports_failed_job() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-2"})],
        get_responses=[FakeResponse(200, {"job_id": "job-2", "status": "failed", "error": "LLM failed"})],
    )
    client = StockSumHttpClient(session=session, poll_seconds=0)

    with pytest.raises(StockSumRequestError, match="LLM failed"):
        await client.run_report(profile="default", output_format="html")


async def test_client_downloads_successful_job_with_warnings() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-warn"})],
        get_responses=[
            FakeResponse(
                200,
                {
                    "job_id": "job-warn",
                    "status": "succeeded",
                    "warnings": [{"section": "collector", "message": "temporary source failure"}],
                },
            ),
            FakeResponse(200, body=b"report", headers={"content-type": "text/markdown; charset=utf-8"}),
        ],
    )
    client = StockSumHttpClient(session=session, poll_seconds=0)

    artifact = await client.run_report(profile="default", output_format="discord")

    assert artifact.content == b"report"
    assert artifact.status["warnings"][0]["section"] == "collector"


async def test_client_retries_transient_poll_disconnect() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-retry"})],
        get_responses=[
            ConnectionError("Server disconnected"),
            FakeResponse(200, {"job_id": "job-retry", "status": "succeeded"}),
            FakeResponse(200, body=b"ok", headers={"content-type": "text/plain; charset=utf-8"}),
        ],
    )
    client = StockSumHttpClient(session=session, poll_seconds=0, timeout_seconds=10)

    artifact = await client.run_report(profile="default", output_format="text")

    assert artifact.job_id == "job-retry"
    assert artifact.content == b"ok"


async def test_client_reports_timeout() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-3"})],
        get_responses=[],
    )
    client = StockSumHttpClient(
        session=session,
        poll_seconds=0,
        timeout_seconds=0,
    )

    with pytest.raises(StockSumRequestError, match="timed out"):
        await client.run_report(profile="default", output_format="html")


async def test_client_maps_blacklist_failure() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(403, {"detail": "Client IP is blacklisted: 10.0.0.5"})],
        get_responses=[],
    )
    client = StockSumHttpClient(session=session)

    with pytest.raises(StockSumRequestError, match="blacklisted"):
        await client.run_report(profile="default", output_format="html")


async def test_client_management_json_methods_send_expected_requests() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(200, {"created": True})],
        get_responses=[FakeResponse(200, {"profiles": []})],
        patch_responses=[FakeResponse(200, {"updated": True})],
        put_responses=[FakeResponse(200, {"set": True})],
        delete_responses=[FakeResponse(200, {"deleted": "x.demo"})],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    assert await client.get_json("/v1/profiles") == {"profiles": []}
    assert await client.post_json("/v1/profiles", payload={"name": "morning"}) == {"created": True}
    assert await client.patch_json("/v1/llm/config", payload={"provider": "deepseek"}) == {"updated": True}
    assert await client.put_json("/v1/secrets/DEEPSEEK_API_KEY", payload={"value": "secret"}) == {"set": True}
    assert await client.delete_json("/v1/sources/x-users/demo") == {"deleted": "x.demo"}

    assert session.requests == [
        ("GET", "http://stock-sum.local/v1/profiles", {"headers": {}}),
        ("POST", "http://stock-sum.local/v1/profiles", {"headers": {}, "json": {"name": "morning"}}),
        ("PATCH", "http://stock-sum.local/v1/llm/config", {"headers": {}, "json": {"provider": "deepseek"}}),
        ("PUT", "http://stock-sum.local/v1/secrets/DEEPSEEK_API_KEY", {"headers": {}, "json": {"value": "secret"}}),
        ("DELETE", "http://stock-sum.local/v1/sources/x-users/demo", {"headers": {}}),
    ]


def test_split_discord_markdown_prefers_blank_lines() -> None:
    chunks = _split_discord_markdown("alpha\n\nbravo\n\ncharlie", limit=14)

    assert chunks == ["alpha\n\nbravo", "charlie"]
    assert all(len(chunk) <= 14 for chunk in chunks)


def test_split_discord_markdown_falls_back_to_lines_and_hard_splits() -> None:
    chunks = _split_discord_markdown("alpha\nbravo\n" + ("x" * 25), limit=11)

    assert chunks == ["alpha\nbravo", "xxxxxxxxxxx", "xxxxxxxxxxx", "xxx"]
    assert all(len(chunk) <= 11 for chunk in chunks)


def test_failure_message_is_truncated() -> None:
    message = _failure_message(RuntimeError("x" * 3000))

    assert message.startswith("stock-sum report failed:")
    assert len(message) <= 1900


async def test_report_command_sends_ack_then_split_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=("first paragraph\n\n" + ("x" * 1950)).encode("utf-8")),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.report(interaction, profile="default", format="discord", private=True)

    assert interaction.response.messages == [
        {
            "content": "Report is being generated, please wait a few minutes.",
            "ephemeral": True,
        }
    ]
    sent_text = [message["content"] for message in interaction.followup.messages]
    assert len(sent_text) == 3
    assert sent_text[0] == "first paragraph"
    assert "job:" not in "\n".join(sent_text)
    assert "format:" not in "\n".join(sent_text)
    assert all(len(item) <= 1900 for item in sent_text)


async def test_report_command_sends_public_discord_report_directly_to_channel(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=("first paragraph\n\nsecond paragraph").encode("utf-8")),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.report(interaction, profile="default", format="discord", private=False)

    assert interaction.response.messages == [
        {
            "content": "Report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.followup.messages == []
    assert interaction.channel.messages == [
        {"content": "first paragraph\n\nsecond paragraph", "suppress_embeds": True},
    ]


async def test_report_command_sends_file_for_non_discord_format(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=b"<html>report</html>", filename="report.html"),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.report(interaction, profile="default", format="html", private=False)

    assert interaction.response.messages[0]["content"] == "Report is being generated, please wait a few minutes."
    assert interaction.followup.messages == []
    assert interaction.channel.messages == [
        {
            "content": "Report generated.",
            "file": "report.html",
            "suppress_embeds": True,
        }
    ]


async def test_report_command_sends_failure_message(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeFailingStockSumClient(),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.report(interaction, profile="default", format="discord", private=True)

    assert interaction.response.messages[0]["content"] == "Report is being generated, please wait a few minutes."
    assert interaction.followup.messages == [
        {
            "content": "stock-sum report failed: broken",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_management_command_blocks_non_owner(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=False))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.sources_add_x(interaction, handle="aleabitoreddit")

    assert client.calls == []
    assert interaction.response.messages == [
        {
            "content": "Only Redbot owners can use this stock-sum command.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_management_source_add_calls_api_for_owner(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.sources_add_x(
        interaction,
        handle="@aleabitoreddit",
        profile="default",
        limit=150,
        lookback_hours=12,
        enabled=True,
    )

    assert client.calls == [
        (
            "post",
            "/v1/sources/x-users",
            {"handle": "@aleabitoreddit", "profile": "default", "limit": 150, "lookback_hours": 12, "enabled": True},
        )
    ]
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "Added X source @aleabitoreddit" in interaction.response.messages[0]["content"]


async def test_secret_set_command_is_ephemeral_and_redacted(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.secrets_set(interaction, name="DEEPSEEK_API_KEY", value="sk-real-secret")

    assert client.calls == [
        ("put", "/v1/secrets/DEEPSEEK_API_KEY", {"value": "sk-real-secret"}),
    ]
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "sk-real-secret" not in interaction.response.messages[0]["content"]


class FakeSession:
    def __init__(
        self,
        *,
        post_responses: list[FakeResponse],
        get_responses: list[FakeResponse | Exception],
        patch_responses: list[FakeResponse] | None = None,
        put_responses: list[FakeResponse] | None = None,
        delete_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.post_responses = post_responses
        self.get_responses = get_responses
        self.patch_responses = patch_responses or []
        self.put_responses = put_responses or []
        self.delete_responses = delete_responses or []
        self.requests: list[tuple[str, str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("POST", url, kwargs))
        return FakeContext(self.post_responses.pop(0))

    def get(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("GET", url, kwargs))
        response = self.get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeContext(response)

    def patch(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("PATCH", url, kwargs))
        return FakeContext(self.patch_responses.pop(0))

    def put(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("PUT", url, kwargs))
        return FakeContext(self.put_responses.pop(0))

    def delete(self, url: str, **kwargs: Any) -> "FakeContext":
        self.requests.append(("DELETE", url, kwargs))
        return FakeContext(self.delete_responses.pop(0))

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


class FakeStockSumClient:
    def __init__(self, *, content: bytes, filename: str = "report.md") -> None:
        self.content = content
        self.filename = filename

    async def run_report(self, *, profile: str, output_format: str) -> StockSumArtifact:
        return StockSumArtifact(
            job_id="job-1",
            filename=self.filename,
            content_type="text/markdown; charset=utf-8",
            content=self.content,
            status={"status": "succeeded"},
        )


class FakeFailingStockSumClient:
    async def run_report(self, *, profile: str, output_format: str) -> StockSumArtifact:
        raise StockSumRequestError("broken")


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponseSender()
        self.followup = FakeFollowupSender()
        self.channel = FakeChannelSender()
        self.user = object()


class FakeResponseSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def is_done(self) -> bool:
        return bool(self.messages)

    async def send_message(
        self,
        content: str,
        *,
        ephemeral: bool,
        file: Any | None = None,
        suppress_embeds: bool = False,
    ) -> None:
        message = {"content": content, "ephemeral": ephemeral}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)


class FakeFollowupSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(
        self,
        content: str,
        *,
        ephemeral: bool,
        file: Any | None = None,
        suppress_embeds: bool = False,
    ) -> None:
        message = {"content": content, "ephemeral": ephemeral}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)


class FakeChannelSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def send(self, content: str, file: Any | None = None, suppress_embeds: bool = False) -> None:
        message = {"content": content}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)


class FakeDiscord:
    class File:
        def __init__(self, fp: Any, *, filename: str) -> None:
            self.fp = fp
            self.filename = filename


class FakeBot:
    def __init__(self, *, owner: bool) -> None:
        self.owner = owner

    async def is_owner(self, _user: object) -> bool:
        return self.owner


class FakeManagementClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get_json(self, path: str) -> dict[str, Any]:
        self.calls.append(("get", path, None))
        return {"ok": True}

    async def post_json(self, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("post", path, payload))
        return {"ok": True}

    async def patch_json(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("patch", path, payload))
        return {"ok": True}

    async def put_json(self, path: str, *, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("put", path, payload))
        return {"name": path.rsplit("/", 1)[-1], "set": True}

    async def delete_json(self, path: str) -> dict[str, Any]:
        self.calls.append(("delete", path, None))
        return {"ok": True}
