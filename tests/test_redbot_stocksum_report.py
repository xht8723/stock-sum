"""Tests for the Redbot stock-sum report cog HTTP bridge."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from redbot_cogs.stocksum_report.stocksum_report import (
    DEFAULT_TIMEOUT_SECONDS,
    StockSumArtifact,
    StockSumReport,
    StockSumHttpClient,
    StockSumRequestError,
    _failure_message,
    _split_discord_markdown,
)


def test_default_report_timeout_is_30_minutes() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 30 * 60


def test_required_slash_command_parameters_are_explicit() -> None:
    required_parameters = {
        "settings_add_x": {"handle"},
        "settings_remove_x": {"handle"},
        "settings_add_reddit": {"subreddit"},
        "settings_remove_reddit": {"subreddit"},
        "plot": {"mode"},
    }
    for method_name, parameter_names in required_parameters.items():
        signature = inspect.signature(getattr(StockSumReport, method_name))
        for parameter_name in parameter_names:
            assert signature.parameters[parameter_name].default is inspect.Parameter.empty


def test_conditional_filter_slash_parameters_stay_optional() -> None:
    conditional_optional_parameters = {
        "ptr_search": {"name", "start_date", "end_date", "days", "asset_type", "ticker"},
        "thirteenf_search": {
            "manager",
            "issuer",
            "cik",
            "accession_number",
            "cusip",
            "figi",
            "put_call",
            "period_start",
            "period_end",
            "filing_start",
            "filing_end",
            "min_value",
            "min_shares",
        },
        "plot": {"ticker", "fuzzy_search", "name", "asset_type", "days", "start_date", "end_date"},
    }
    for method_name, parameter_names in conditional_optional_parameters.items():
        signature = inspect.signature(getattr(StockSumReport, method_name))
        for parameter_name in parameter_names:
            assert signature.parameters[parameter_name].default is not inspect.Parameter.empty


def test_report_commands_do_not_expose_format_parameter() -> None:
    for method_name in ("recent_posts", "ptr_search", "thirteenf_search"):
        assert "format" not in inspect.signature(getattr(StockSumReport, method_name)).parameters


def test_recent_posts_does_not_expose_backend_reddit_method() -> None:
    assert "method" not in inspect.signature(StockSumReport.recent_posts).parameters
    assert "x_method" not in inspect.signature(StockSumReport.recent_posts).parameters
    assert "reddit_method" not in inspect.signature(StockSumReport.recent_posts).parameters


def test_discord_command_names_match_public_contract() -> None:
    source = inspect.getsource(StockSumReport)

    assert '@app_commands.command(name="recent_posts"' in source
    assert '@app_commands.command(name="ptr_search"' in source
    assert '@app_commands.command(name="13f_search"' in source
    assert '@app_commands.command(name="trendings"' in source
    assert '@app_commands.command(name="plot"' in source
    assert '@app_commands.command(name="help"' in source
    assert "def help(" not in source


def test_stocksum_group_is_removed_and_settings_group_exists() -> None:
    assert not hasattr(StockSumReport, "stocksum")
    assert hasattr(StockSumReport, "settings")


async def test_help_command_lists_available_commands() -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)

    await report.stocksum_help(interaction)

    assert interaction.response.messages
    message = interaction.response.messages[0]
    assert message["ephemeral"] is False
    assert message["suppress_embeds"] is True
    for command in (
        "/recent_posts",
        "/ptr_search",
        "/13f_search",
        "/trendings",
        "/plot",
        "/settings list",
        "/settings add-x",
        "/settings remove-x",
        "/settings add-reddit",
        "/settings remove-reddit",
        "/help",
    ):
        assert command in message["content"]


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

    artifact = await client.run_social_report(output_format="html")

    assert artifact.job_id == "job-1"
    assert artifact.filename == "report.html"
    assert artifact.content == b"<html>ok</html>"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/social-reports/jobs/html",
        {
            "headers": {},
            "json": {"detail": "minimum"},
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

    artifact = await client.run_social_report(output_format="discord")

    assert artifact.filename == "stock-sum-report-job-discord.md"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/social-reports/jobs/discord",
        {
            "headers": {},
            "json": {"detail": "minimum"},
        },
    )


async def test_client_sends_trading_report_request_and_downloads_artifact() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "trade-1"})],
        get_responses=[
            FakeResponse(200, {"job_id": "trade-1", "status": "succeeded"}),
            FakeResponse(
                200,
                body=b"**Official Trading Disclosures**",
                headers={"content-type": "text/markdown; charset=utf-8"},
            ),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    artifact = await client.run_trading_report(
        output_format="discord",
        name="Pelosi",
        days=30,
        asset_type="ST",
        ticker="AMZN",
        limit=25,
        force_refresh=True,
    )

    assert artifact.job_id == "trade-1"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trading-reports/jobs/discord",
        {
            "headers": {},
            "json": {"name": "Pelosi", "days": 30, "asset_type": "ST", "ticker": "AMZN", "limit": 25, "force_refresh": True},
        },
    )


async def test_client_omits_trading_limit_by_default() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "trade-2"})],
        get_responses=[
            FakeResponse(200, {"job_id": "trade-2", "status": "succeeded"}),
            FakeResponse(200, body=b"trades", headers={"content-type": "text/markdown; charset=utf-8"}),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    await client.run_trading_report(output_format="discord", days=30)

    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trading-reports/jobs/discord",
        {
            "headers": {},
            "json": {"days": 30, "force_refresh": False},
        },
    )


async def test_client_rejects_trading_report_without_filter() -> None:
    client = StockSumHttpClient(session=FakeSession(post_responses=[], get_responses=[]), poll_seconds=0)

    with pytest.raises(StockSumRequestError, match="requires at least one filter"):
        await client.run_trading_report(output_format="discord")


async def test_client_omits_13f_limit_by_default() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "13f-2"})],
        get_responses=[
            FakeResponse(200, {"job_id": "13f-2", "status": "succeeded"}),
            FakeResponse(200, body=b"holdings", headers={"content-type": "text/markdown; charset=utf-8"}),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    await client.run_13f_report(output_format="discord", issuer="NVIDIA")

    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/13f-reports/jobs/discord",
        {
            "headers": {},
            "json": {"issuer": "NVIDIA", "force_refresh": False},
        },
    )


async def test_client_does_not_clip_large_13f_limit() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "13f-3"})],
        get_responses=[
            FakeResponse(200, {"job_id": "13f-3", "status": "succeeded"}),
            FakeResponse(200, body=b"holdings", headers={"content-type": "text/markdown; charset=utf-8"}),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    await client.run_13f_report(output_format="discord", issuer="NVIDIA", limit=5000)

    assert session.requests[0][2]["json"]["limit"] == 5000


async def test_client_sends_statistic_request_and_downloads_png() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "stat-1"})],
        get_responses=[
            FakeResponse(200, {"job_id": "stat-1", "status": "succeeded"}),
            FakeResponse(
                200,
                body=b"png",
                headers={
                    "content-type": "image/png",
                    "content-disposition": 'attachment; filename="statistic.png"',
                },
            ),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    artifact = await client.run_statistic(mode="social", ticker="NVDA", days=30)

    assert artifact.filename == "statistic.png"
    assert artifact.content_type == "image/png"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/statistics/jobs",
        {
            "headers": {},
            "json": {
                "mode": "social",

                "ticker": "NVDA",
                "action": "all",
                "source": "all",
                "sentiment": "all",
                "days": 30,
                "bucket": "auto",
            },
        },
    )


async def test_client_sends_trendings_request_and_downloads_artifact() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "trend-1"})],
        get_responses=[
            FakeResponse(200, {"job_id": "trend-1", "status": "succeeded"}),
            FakeResponse(
                200,
                body=b"**Trending stocks**",
                headers={"content-type": "text/markdown; charset=utf-8"},
            ),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    artifact = await client.run_trendings_report(
        output_format="discord",
        from_date="2026-07-01",
        to_date="2026-07-06",
        limit=3,
    )

    assert artifact.filename == "stock-sum-report-trend-1.md"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trendings/jobs/discord",
        {
            "headers": {},
            "json": {"from": "2026-07-01", "to": "2026-07-06", "limit": 3},
        },
    )


async def test_client_reports_failed_job() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-2"})],
        get_responses=[FakeResponse(200, {"job_id": "job-2", "status": "failed", "error": "LLM failed"})],
    )
    client = StockSumHttpClient(session=session, poll_seconds=0)

    with pytest.raises(StockSumRequestError, match="LLM failed"):
        await client.run_social_report(output_format="html")


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

    artifact = await client.run_social_report(output_format="discord")

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

    artifact = await client.run_social_report(output_format="text")

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
        await client.run_social_report(output_format="html")


async def test_client_maps_blacklist_failure() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(403, {"detail": "Client IP is blacklisted: 10.0.0.5"})],
        get_responses=[],
    )
    client = StockSumHttpClient(session=session)

    with pytest.raises(StockSumRequestError, match="blacklisted"):
        await client.run_social_report(output_format="html")


async def test_client_management_json_methods_send_expected_requests() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(200, {"created": True})],
        get_responses=[FakeResponse(200, {"x_users": []})],
        delete_responses=[FakeResponse(200, {"deleted": "x.demo"})],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    assert await client.get_json("/v1/sources/x-users") == {"x_users": []}
    assert await client.post_json("/v1/sources/x-users", payload={"handle": "demo"}) == {"created": True}
    assert await client.delete_json("/v1/sources/x-users/demo") == {"deleted": "x.demo"}

    assert session.requests == [
        ("GET", "http://stock-sum.local/v1/sources/x-users", {"headers": {}}),
        ("POST", "http://stock-sum.local/v1/sources/x-users", {"headers": {}, "json": {"handle": "demo"}}),
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

    await report.recent_posts(interaction)

    assert interaction.response.messages == [
        {
            "content": "Social report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    sent_text = [message["content"] for message in interaction.channel.messages]
    assert len(sent_text) == 3
    assert sent_text[0] == "first paragraph"
    assert "job:" not in "\n".join(sent_text)
    assert "format:" not in "\n".join(sent_text)
    assert all(len(item) <= 1900 for item in sent_text)


async def test_report_command_sends_public_discord_report_directly_to_channel(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=("first paragraph\n\nsecond paragraph").encode("utf-8"))
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: client,
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.recent_posts(interaction, detail="medium")

    assert client.social_calls == [{ "output_format": "discord", "detail": "medium"}]
    assert interaction.response.messages == [
        {
            "content": "Social report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.followup.messages == []
    assert interaction.channel.messages == [
        {"content": "first paragraph\n\nsecond paragraph", "suppress_embeds": True},
    ]


async def test_report_command_sends_failure_message(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeFailingStockSumClient(),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.recent_posts(interaction)

    assert interaction.response.messages[0]["content"] == "Social report is being generated, please wait a few minutes."
    assert interaction.channel.messages == [
        {
            "content": "stock-sum report failed: broken",
            "suppress_embeds": True,
        }
    ]


async def test_trendings_command_sends_public_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"**Trending stocks**\n- NVDA")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.trendings(interaction, from_date="2026-07-01", to_date="2026-07-06", limit=3)

    assert client.trendings_calls == [
        {
            "output_format": "discord",
            "from_date": "2026-07-01",
            "to_date": "2026-07-06",
            "limit": 3,
        }
    ]
    assert interaction.response.messages == [
        {
            "content": "Trendings report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.channel.messages == [
        {"content": "**Trending stocks**\n- NVDA", "suppress_embeds": True},
    ]


async def test_trendings_command_rejects_invalid_date_and_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.trendings(interaction, from_date="bad-date", limit=0)

    assert client.trendings_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: from must be in YYYY-MM-DD format.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_ptr_search_command_rejects_missing_filters(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=b"unused"),
    )

    await report.ptr_search(interaction)

    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: ptr_search requires at least one filter: name, start_date/end_date, days, asset_type, or ticker.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]
    assert interaction.followup.messages == []


async def test_ptr_search_rejects_invalid_date_and_limit_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.ptr_search(
        interaction,
        name="Pelosi",
        start_date="2026-13-01",
        limit=0,
    )

    assert client.trading_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: start_date must be in YYYY-MM-DD format.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]
    assert interaction.followup.messages == []


async def test_ptr_search_rejects_unknown_asset_type_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.ptr_search(interaction, asset_type="bad!")

    assert client.trading_calls == []
    assert "asset_type must be" in interaction.response.messages[0]["content"]


async def test_ptr_search_command_sends_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=("trade one\n\ntrade two").encode("utf-8"))
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.ptr_search(
        interaction,
        name="Pelosi",
        days=30,
        asset_type="st",
        ticker="amzn",
        limit=25,
        force_refresh=True,
    )

    assert client.trading_calls == [
        {
            "output_format": "discord",
            "name": "Pelosi",
            "start_date": None,
            "end_date": None,
            "days": 30,
            "asset_type": "ST",
            "ticker": "AMZN",
            "limit": 25,
            "force_refresh": True,
        }
    ]
    assert interaction.response.messages == [
        {
            "content": "Trading disclosure report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.channel.messages == [{"content": "trade one\n\ntrade two", "suppress_embeds": True}]


async def test_ptr_search_command_omits_limit_when_unset(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"trade")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.ptr_search(interaction, days=30)

    assert client.trading_calls[0]["output_format"] == "discord"
    assert client.trading_calls[0]["limit"] is None


async def test_ptr_search_command_does_not_clip_large_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"trade")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.ptr_search(interaction, days=30, limit=5000)

    assert client.trading_calls[0]["output_format"] == "discord"
    assert client.trading_calls[0]["limit"] == 5000


async def test_13f_search_rejects_invalid_filters_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.thirteenf_search(
        interaction,
        issuer="NVIDIA",
        period_start="2026-04-01",
        period_end="2026-03-31",
        limit=101,
    )

    assert client.sec_13f_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: period_start must be on or before period_end.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]
    assert interaction.followup.messages == []


async def test_13f_search_rejects_invalid_limit_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.thirteenf_search(interaction, issuer="NVIDIA", limit=0)

    assert client.sec_13f_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: limit must be 1 or greater.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]
    assert interaction.followup.messages == []


async def test_13f_search_command_sends_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=("holding one\n\nholding two").encode("utf-8"))
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.thirteenf_search(
        interaction,
        manager="Berkshire",
        issuer="nvidia",
        cik="1067983",
        cusip="67066g104",
        put_call="call",
        period_start="2026-01-01",
        period_end="2026-03-31",
        min_value=1000,
        limit=25,
        force_refresh=True,
    )

    assert client.sec_13f_calls == [
        {
            "output_format": "discord",
            "manager": "Berkshire",
            "issuer": "nvidia",
            "cik": "1067983",
            "accession_number": None,
            "cusip": "67066G104",
            "figi": None,
            "put_call": "CALL",
            "period_start": "2026-01-01",
            "period_end": "2026-03-31",
            "filing_start": None,
            "filing_end": None,
            "min_value": 1000,
            "min_shares": None,
            "limit": 25,
            "force_refresh": True,
        }
    ]
    assert interaction.response.messages == [
        {
            "content": "SEC 13F report is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.channel.messages == [{"content": "holding one\n\nholding two", "suppress_embeds": True}]


async def test_13f_search_command_omits_limit_when_unset(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"holding")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.thirteenf_search(interaction, issuer="NVIDIA")

    assert client.sec_13f_calls[0]["output_format"] == "discord"
    assert client.sec_13f_calls[0]["limit"] is None


async def test_13f_search_command_does_not_clip_large_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"holding")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.thirteenf_search(interaction, issuer="NVIDIA", limit=5000)

    assert client.sec_13f_calls[0]["output_format"] == "discord"
    assert client.sec_13f_calls[0]["limit"] == 5000


async def test_plot_rejects_invalid_parameters_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.plot(interaction, mode="social", ticker="bad ticker!", days=30)

    assert client.statistic_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: ticker must be 1-16 characters using letters, numbers, dot, or dash.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_plot_command_sends_png_file(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"png", filename="statistic.png")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.plot(
        interaction,
        mode="trading",
        ticker="aapl",
        name="Pelosi",
        asset_type="st",
        action="sell",
        days=180,
        bucket="week",
    )

    assert client.statistic_calls == [
        {
            "mode": "trading",

            "ticker": "AAPL",
            "fuzzy_tag": None,
            "name": "Pelosi",
            "asset_name": None,
            "asset_type": "ST",
            "action": "sell",
            "source": "all",
            "sentiment": "all",
            "days": 180,
            "start_date": None,
            "end_date": None,
            "bucket": "week",
        }
    ]
    assert interaction.response.messages == [
        {
            "content": "Statistic chart is being generated, please wait a few minutes.",
            "ephemeral": False,
        }
    ]
    assert interaction.channel.messages == [
        {
            "content": "Statistic generated.",
            "file": "statistic.png",
            "suppress_embeds": True,
        }
    ]


async def test_plot_rejects_ticker_and_fuzzy_search_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot())
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.plot(interaction, mode="social", ticker="NVDA", fuzzy_search="nvidia", days=30)

    assert client.fuzzy_calls == []
    assert client.statistic_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: Use either ticker or fuzzy_search, not both.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_plot_fuzzy_search_selects_social_tag_with_reaction(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(reaction_emoji="2️⃣"))
    client = FakeStockSumClient(content=b"png", filename="statistic.png")
    client.fuzzy_matches = [
        {
            "mode": "social",
            "label": "ai",
            "row_count": 4,
            "x_count": 2,
            "reddit_count": 2,
            "statistic_filters": {"fuzzy_tag": "ai"},
        },
        {
            "mode": "social",
            "label": "nvidia",
            "row_count": 17,
            "x_count": 9,
            "reddit_count": 8,
            "statistic_filters": {"fuzzy_tag": "nvidia"},
        },
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.plot(interaction, mode="social", fuzzy_search="NVIDIA", days=30)

    assert client.fuzzy_calls == [{"mode": "social",  "query": "NVIDIA", "limit": 5}]
    assert client.statistic_calls == [
        {
            "mode": "social",

            "ticker": None,
            "fuzzy_tag": "nvidia",
            "name": None,
            "asset_name": None,
            "asset_type": None,
            "action": "all",
            "source": "all",
            "sentiment": "all",
            "days": 30,
            "start_date": None,
            "end_date": None,
            "bucket": "auto",
        }
    ]
    assert interaction.response.deferred == [{"ephemeral": False, "thinking": True}]
    assert "Select a fuzzy_search match" in interaction.channel.messages[0]["content"]
    assert interaction.channel.sent_messages[0].reactions == ["1️⃣", "2️⃣"]
    assert interaction.channel.messages[1]["content"] == "Selected: nvidia. Generating statistic chart..."
    assert interaction.channel.messages[2]["content"] == "Statistic chart is being generated, please wait a few minutes."
    assert interaction.channel.messages[3]["file"] == "statistic.png"


async def test_plot_fuzzy_search_accepts_plain_digit_reaction(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(reaction_emoji="2"))
    client = FakeStockSumClient(content=b"png", filename="statistic.png")
    client.fuzzy_matches = [
        {
            "mode": "social",
            "label": "ai",
            "row_count": 1,
            "x_count": 1,
            "reddit_count": 0,
            "statistic_filters": {"fuzzy_tag": "ai"},
        },
        {
            "mode": "social",
            "label": "openai",
            "row_count": 2,
            "x_count": 0,
            "reddit_count": 2,
            "statistic_filters": {"fuzzy_tag": "openai"},
        },
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.plot(interaction, mode="social", fuzzy_search="AI", days=30)

    assert client.statistic_calls[0]["fuzzy_tag"] == "openai"
    assert interaction.channel.messages[1]["content"] == "Selected: openai. Generating statistic chart..."


async def test_plot_fuzzy_search_timeout_edits_selection_message(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(timeout=True))
    client = FakeStockSumClient(content=b"unused")
    client.fuzzy_matches = [
        {
            "mode": "trading",
            "label": "Apple Inc. - Common Stock (AAPL) [ST]",
            "row_count": 3,
            "ticker": "AAPL",
            "asset_type_code": "ST",
            "statistic_filters": {"asset_name": "Apple Inc. - Common Stock (AAPL) [ST]", "ticker": "AAPL"},
        }
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.plot(interaction, mode="trading", fuzzy_search="Apple", days=180)

    assert client.statistic_calls == []
    assert interaction.channel.messages[0]["content"] == "Select a fuzzy_search match for `Apple`:\n1️⃣ Apple Inc. - Common Stock (AAPL) [ST] - 3 rows, AAPL, ST\nClick one of the numbered reactions below to choose."
    assert interaction.channel.sent_messages[0].edits == ["Selection timed out. Run /plot again to retry."]


async def test_plot_fuzzy_search_ignores_other_user_reaction(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(reaction_user_id=999))
    client = FakeStockSumClient(content=b"unused")
    client.fuzzy_matches = [
        {
            "mode": "social",
            "label": "ai",
            "row_count": 1,
            "x_count": 1,
            "reddit_count": 0,
            "statistic_filters": {"fuzzy_tag": "ai"},
        }
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.plot(interaction, mode="social", fuzzy_search="AI", days=30)

    assert client.statistic_calls == []
    assert interaction.channel.sent_messages[0].edits == ["Selection timed out. Run /plot again to retry."]


async def test_plot_fuzzy_search_ignores_unsupported_reaction(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(reaction_emoji="🐍"))
    client = FakeStockSumClient(content=b"unused")
    client.fuzzy_matches = [
        {
            "mode": "social",
            "label": "ai",
            "row_count": 1,
            "x_count": 1,
            "reddit_count": 0,
            "statistic_filters": {"fuzzy_tag": "ai"},
        }
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.plot(interaction, mode="social", fuzzy_search="AI", days=30)

    assert client.statistic_calls == []
    assert interaction.channel.sent_messages[0].edits == ["Selection timed out. Run /plot again to retry."]


async def test_plot_fuzzy_search_reaction_add_failure_does_not_crash(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(reaction_emoji="1"))
    client = FakeStockSumClient(content=b"png", filename="statistic.png")
    client.fuzzy_matches = [
        {
            "mode": "social",
            "label": "ai",
            "row_count": 1,
            "x_count": 1,
            "reddit_count": 0,
            "statistic_filters": {"fuzzy_tag": "ai"},
        }
    ]

    async def failing_add_reaction(self, emoji: str) -> None:
        raise RuntimeError("missing permission")

    monkeypatch.setattr(FakeMessage, "add_reaction", failing_add_reaction)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.plot(interaction, mode="social", fuzzy_search="AI", days=30)

    assert client.statistic_calls[0]["fuzzy_tag"] == "ai"
    assert interaction.channel.messages[1]["content"] == "Selected: ai. Generating statistic chart..."


async def test_plot_fuzzy_search_defer_failure_still_posts_channel_prompt(monkeypatch) -> None:
    interaction = FakeInteraction()
    interaction.response.fail_defer = True
    report = StockSumReport(bot=FakeBot(reaction_emoji="1"))
    client = FakeStockSumClient(content=b"png", filename="statistic.png")
    client.fuzzy_matches = [
        {
            "mode": "trading",
            "label": "OpenAI Global LLC",
            "row_count": 1,
            "ticker": "",
            "asset_type_code": "OI",
            "statistic_filters": {"asset_name": "OpenAI Global LLC [OI]"},
        }
    ]
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.discord", FakeDiscord)

    await report.plot(interaction, mode="trading", fuzzy_search="openai", days=30)

    assert "Select a fuzzy_search match" in interaction.channel.messages[0]["content"]
    assert interaction.channel.sent_messages[0].reactions == ["1️⃣"]
    assert client.statistic_calls[0]["asset_name"] == "OpenAI Global LLC [OI]"


async def test_settings_command_blocks_non_owner(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=False))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_add_x(interaction, handle="aleabitoreddit")

    assert client.calls == []
    assert interaction.response.messages == [
        {
            "content": "Only Redbot owners can use this stock-sum command.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_settings_list_formats_sources(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=False))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_list(interaction)

    assert client.calls == [("get", "/v1/sources", None)]
    assert interaction.response.messages == [
        {
            "content": "**Stock-Sum Sources**\n\n**X users**\n- @aleabitoreddit (enabled, fetch cap: 100, lookback: 24h)\n\n**Subreddits**\n- r/wallstreetbets (enabled, fetch cap: 100, lookback: 24h, comments: 10)",
            "ephemeral": False,
            "suppress_embeds": True,
        }
    ]


async def test_settings_source_add_calls_api_for_owner(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_add_x(
        interaction,
        handle="@aleabitoreddit",
    )

    assert client.calls == [
        (
            "post",
            "/v1/sources/x-users",
            {"handle": "@aleabitoreddit"},
        )
    ]
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "Added X source @aleabitoreddit" in interaction.response.messages[0]["content"]


async def test_settings_source_add_rejects_invalid_handle(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_add_x(interaction, handle="bad/handle")

    assert client.calls == []
    assert "X handle must be" in interaction.response.messages[0]["content"]


async def test_settings_reddit_source_add_uses_endpoint_defaults(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_add_reddit(interaction, subreddit="wallstreetbets")

    assert client.calls == [
        (
            "post",
            "/v1/sources/subreddits",
            {"subreddit": "wallstreetbets"},
        )
    ]
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "Added subreddit wallstreetbets" in interaction.response.messages[0]["content"]


async def test_settings_delete_sources(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.stocksum_report.StockSumHttpClient.from_env", lambda: client)

    await report.settings_remove_x(interaction, handle="@aleabitoreddit")
    await report.settings_remove_reddit(interaction, subreddit="r/wallstreetbets")

    assert client.calls == [
        ("delete", "/v1/sources/x-users/%40aleabitoreddit", None),
        ("delete", "/v1/sources/subreddits/r%2Fwallstreetbets", None),
    ]
    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.followup.messages[0]["ephemeral"] is True


class FakeSession:
    def __init__(
        self,
        *,
        post_responses: list[FakeResponse],
        get_responses: list[FakeResponse | Exception],
        delete_responses: list[FakeResponse] | None = None,
    ) -> None:
        self.post_responses = post_responses
        self.get_responses = get_responses
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
        self.social_calls: list[dict[str, Any]] = []
        self.trading_calls: list[dict[str, Any]] = []
        self.sec_13f_calls: list[dict[str, Any]] = []
        self.trendings_calls: list[dict[str, Any]] = []
        self.statistic_calls: list[dict[str, Any]] = []
        self.fuzzy_matches: list[dict[str, Any]] = []
        self.fuzzy_calls: list[dict[str, Any]] = []

    async def run_social_report(self, *, output_format: str, detail: str = "minimum") -> StockSumArtifact:
        self.social_calls.append({"output_format": output_format, "detail": detail})
        return StockSumArtifact(
            job_id="job-1",
            filename=self.filename,
            content_type="text/markdown; charset=utf-8",
            content=self.content,
            status={"status": "succeeded"},
        )

    async def run_trading_report(
        self,
        *,
        output_format: str,
        name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        days: int | None = None,
        asset_type: str | None = None,
        ticker: str | None = None,
        limit: int | None = None,
        force_refresh: bool = False,
    ) -> StockSumArtifact:
        self.trading_calls.append(
            {
                "output_format": output_format,
                "name": name,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
                "asset_type": asset_type,
                "ticker": ticker,
                "limit": limit,
                "force_refresh": force_refresh,
            }
        )
        return StockSumArtifact(
            job_id="trade-1",
            filename=self.filename,
            content_type="text/markdown; charset=utf-8",
            content=self.content,
            status={"status": "succeeded"},
        )

    async def run_13f_report(self, **kwargs: Any) -> StockSumArtifact:
        self.sec_13f_calls.append(kwargs)
        return StockSumArtifact(
            job_id="13f-1",
            filename=self.filename,
            content_type="text/markdown; charset=utf-8",
            content=self.content,
            status={"status": "succeeded"},
        )

    async def run_statistic(self, **kwargs: Any) -> StockSumArtifact:
        self.statistic_calls.append(kwargs)
        return StockSumArtifact(
            job_id="stat-1",
            filename=self.filename,
            content_type="image/png",
            content=self.content,
            status={"status": "succeeded"},
        )

    async def run_trendings_report(self, **kwargs: Any) -> StockSumArtifact:
        self.trendings_calls.append(kwargs)
        return StockSumArtifact(
            job_id="trend-1",
            filename=self.filename,
            content_type="text/markdown; charset=utf-8",
            content=self.content,
            status={"status": "succeeded"},
        )

    async def statistic_fuzzy_matches(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.fuzzy_calls.append(kwargs)
        return self.fuzzy_matches


class FakeFailingStockSumClient:
    async def run_social_report(self, *, output_format: str, detail: str = "minimum") -> StockSumArtifact:
        raise StockSumRequestError("broken")

    async def run_trading_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError("broken")

    async def run_13f_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError("broken")

    async def run_statistic(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError("broken")

    async def run_trendings_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError("broken")


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponseSender()
        self.followup = FakeFollowupSender()
        self.channel = FakeChannelSender()
        self.user = FakeUser(100)


class FakeResponseSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.sent_messages: list[FakeMessage] = []
        self.deferred: list[dict[str, Any]] = []
        self.fail_defer = False

    def is_done(self) -> bool:
        return bool(self.messages or self.deferred)

    async def defer(self, *, ephemeral: bool, thinking: bool = False) -> None:
        if self.fail_defer:
            raise RuntimeError("defer failed")
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})

    async def send_message(
        self,
        content: str,
        *,
        ephemeral: bool,
        file: Any | None = None,
        suppress_embeds: bool = False,
    ) -> Any:
        message = {"content": content, "ephemeral": ephemeral}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)
        sent_message = FakeMessage(content)
        self.sent_messages.append(sent_message)
        return sent_message


class FakeFollowupSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.sent_messages: list[FakeMessage] = []

    async def send(
        self,
        content: str,
        *,
        ephemeral: bool,
        file: Any | None = None,
        suppress_embeds: bool = False,
        wait: bool = False,
    ) -> Any:
        message = {"content": content, "ephemeral": ephemeral}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)
        sent_message = FakeMessage(content)
        self.sent_messages.append(sent_message)
        return sent_message if wait else None


class FakeChannelSender:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.sent_messages: list[FakeMessage] = []

    async def send(self, content: str, file: Any | None = None, suppress_embeds: bool = False) -> Any:
        message = {"content": content}
        if file is not None:
            message["file"] = file.filename
        if suppress_embeds:
            message["suppress_embeds"] = suppress_embeds
        self.messages.append(message)
        sent_message = FakeMessage(content)
        self.sent_messages.append(sent_message)
        return sent_message


class FakeDiscord:
    class File:
        def __init__(self, fp: Any, *, filename: str) -> None:
            self.fp = fp
            self.filename = filename


class FakeBot:
    def __init__(self, *, owner: bool = True, reaction_emoji: str = "1️⃣", reaction_user_id: int = 100, timeout: bool = False) -> None:
        self.owner = owner
        self.reaction_emoji = reaction_emoji
        self.reaction_user_id = reaction_user_id
        self.timeout = timeout

    async def is_owner(self, _user: object) -> bool:
        return self.owner

    async def wait_for(self, _event: str, *, timeout: float, check: Any) -> Any:
        if self.timeout:
            raise asyncio.TimeoutError
        message_id = FakeMessage._last_id
        reaction = FakeRawReaction(self.reaction_emoji, message_id=message_id, user_id=self.reaction_user_id)
        if check(reaction):
            return reaction
        raise asyncio.TimeoutError


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeReaction:
    def __init__(self, emoji: str) -> None:
        self.emoji = emoji
        self.message = None


class FakeRawReaction:
    def __init__(self, emoji: str, *, message_id: int, user_id: int) -> None:
        self.emoji = emoji
        self.message_id = message_id
        self.user_id = user_id


class FakeMessage:
    _next_id = 1
    _last_id = 0

    def __init__(self, content: str) -> None:
        self.id = FakeMessage._next_id
        FakeMessage._next_id += 1
        FakeMessage._last_id = self.id
        self.content = content
        self.reactions: list[str] = []
        self.edits: list[str] = []

    async def add_reaction(self, emoji: str) -> None:
        self.reactions.append(emoji)

    async def edit(self, *, content: str) -> None:
        self.content = content
        self.edits.append(content)


class FakeManagementClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def get_json(self, path: str) -> dict[str, Any]:
        self.calls.append(("get", path, None))
        if path == "/v1/sources":
            return {
                "x_users": [{"handle": "aleabitoreddit", "enabled": True, "limit": 100, "lookback_hours": 24}],
                "subreddits": [
                    {
                        "subreddit": "wallstreetbets",
                        "enabled": True,
                        "limit": 100,
                        "lookback_hours": 24,
                        "comments_per_post": 10,
                    }
                ],
            }
        return {"ok": True}

    async def post_json(self, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append(("post", path, payload))
        return {"ok": True}

    async def delete_json(self, path: str) -> dict[str, Any]:
        self.calls.append(("delete", path, None))
        return {"ok": True}
