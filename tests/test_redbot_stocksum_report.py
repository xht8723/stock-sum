"""Tests for the Redbot stock-sum report cog HTTP bridge."""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from redbot_cogs.stocksum_report.cog import (
    DEFAULT_TIMEOUT_SECONDS,
    StockSumArtifact,
    StockSumReport,
    StockSumHttpClient,
    StockSumRequestError,
    _failure_message,
    _send_command_output,
    _send_report_output,
    _split_discord_markdown,
)
from redbot_cogs.stocksum_report.daily import (
    DAILY_MESSAGE_LIMIT,
    DAILY_SOCIAL_DISPLAY_LIMIT,
    DailyReportSection,
    _format_daily_report,
)


def test_default_report_timeout_is_30_minutes() -> None:
    assert DEFAULT_TIMEOUT_SECONDS == 30 * 60


def test_cog_imports_from_redbot_addpath_layout() -> None:
    addpath = Path("redbot_cogs").resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                f"sys.path.insert(0, {str(addpath)!r}); "
                "import stocksum_report.cog as cog; "
                "assert cog.StockSumReport"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_required_slash_command_parameters_are_explicit() -> None:
    required_parameters = {
        "settings_add_x": {"handle"},
        "settings_remove_x": {"handle"},
        "settings_add_reddit": {"subreddit"},
        "settings_remove_reddit": {"subreddit"},
        "daily": {"time"},
        "plot": {"mode"},
    }
    for method_name, parameter_names in required_parameters.items():
        signature = inspect.signature(getattr(StockSumReport, method_name))
        for parameter_name in parameter_names:
            assert signature.parameters[parameter_name].default is inspect.Parameter.empty


def test_conditional_filter_slash_parameters_stay_optional() -> None:
    conditional_optional_parameters = {
        "ptr_search": {
            "name",
            "start_date",
            "end_date",
            "days",
            "filing_start_date",
            "filing_end_date",
            "filing_days",
            "asset_type",
            "ticker",
        },
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
    assert '@app_commands.command(name="daily"' in source
    assert '@app_commands.command(name="cancel_daily"' in source
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
    for command in (
        "/recent_posts",
        "/ptr_search",
        "/13f_search",
        "/trendings",
        "/plot",
        "/daily",
        "/cancel_daily",
        "/settings list",
        "/settings add-x",
        "/settings remove-x",
        "/settings add-reddit",
        "/settings remove-reddit",
        "/help",
    ):
        assert command in message["content"]


async def test_daily_command_stores_utc_subscription() -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)

    await report.daily(interaction, "09:30")

    subscriptions = await report._daily_store.all_subscriptions()
    subscription = subscriptions[100]
    assert subscription["enabled"] is True
    assert subscription["time_utc"] == "09:30"
    assert subscription["last_sent_utc_date"] == ""
    assert interaction.response.messages == [
        {"content": "Daily stock-sum DM report enabled for 09:30 UTC.", "ephemeral": True, "suppress_embeds": True}
    ]


async def test_daily_command_rejects_invalid_time() -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)

    await report.daily(interaction, "24:00")

    assert "daily time must be UTC HH:MM" in interaction.response.messages[0]["content"]
    assert await report._daily_store.all_subscriptions() == {}


async def test_cancel_daily_disables_subscription() -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)

    await report.daily(interaction, "09:30")
    await report.cancel_daily(interaction)

    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["enabled"] is False
    assert interaction.followup.messages == [
        {"content": "Daily stock-sum DM report canceled.", "ephemeral": True, "suppress_embeds": True}
    ]


async def test_command_output_omits_none_file_keyword() -> None:
    interaction = StrictFileNoneInteraction()

    await _send_command_output(interaction, "ok", private=True)

    assert interaction.response.calls == [("ok", {"ephemeral": True, "suppress_embeds": True})]


async def test_report_output_omits_none_file_keyword_for_followup() -> None:
    interaction = StrictFileNoneInteraction(channel=None)

    await _send_report_output(interaction, "ok", private=False)

    assert interaction.followup.calls == [("ok", {"ephemeral": False, "suppress_embeds": True})]


async def test_due_daily_report_runs_once_per_utc_day() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")
    now = datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc)

    await report._run_due_daily_reports_once(now=now, client=client)
    await report._run_due_daily_reports_once(now=now, client=client)

    assert client.calls == [
        ("trendings", {"output_format": "json"}),
        ("social", {"output_format": "json", "detail": "minimum"}),
        ("trading", {"output_format": "json", "collected_days": 1, "allow_empty": True}),
    ]
    assert len(user.dm_messages) == 4
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"


async def test_daily_report_waits_until_prestart_window() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")

    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 7, 8, 59, tzinfo=timezone.utc),
        client=client,
    )

    assert client.calls == []
    assert user.dm_messages == []


async def test_daily_report_starts_thirty_minutes_before_set_time() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")

    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 7, 9, 0, tzinfo=timezone.utc),
        client=client,
    )

    assert [name for name, _kwargs in client.calls] == ["trendings", "social", "trading"]
    assert len(user.dm_messages) == 4
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"


async def test_daily_report_starts_immediately_when_set_time_is_within_thirty_minutes() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")

    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 7, 9, 5, tzinfo=timezone.utc),
        client=client,
    )

    assert [name for name, _kwargs in client.calls] == ["trendings", "social", "trading"]
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"


async def test_daily_report_midnight_prestart_marks_target_utc_date() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="00:10")

    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 6, 23, 40, tzinfo=timezone.utc),
        client=client,
    )
    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 7, 0, 10, tzinfo=timezone.utc),
        client=client,
    )

    assert len(client.calls) == 3
    assert len(user.dm_messages) == 4
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"
    assert "Report date: `2026-07-07`" in user.dm_messages[0]["content"]


async def test_daily_report_keeps_order_and_continues_after_job_failure() -> None:
    user = FakeUser(100)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient(fail_methods={"social"})
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")

    await report._run_due_daily_reports_once(
        now=datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc),
        client=client,
    )

    assert client.calls == [
        ("trendings", {"output_format": "json"}),
        ("social", {"output_format": "json", "detail": "minimum"}),
        ("trading", {"output_format": "json", "collected_days": 1, "allow_empty": True}),
    ]
    messages = [message["content"] for message in user.dm_messages]
    assert messages[0].startswith("**Stock-Sum Daily Brief**")
    assert messages[1].startswith("**Market Trends**")
    assert messages[2].startswith("**High-Priority Social Signals**")
    assert messages[3].startswith("**House PTR Disclosures**")
    assert "NVDA" in messages[1]
    assert "Unavailable: High-Priority Social Signals failed: social broken" in messages[2]
    assert "AAPL" in messages[3]


async def test_daily_dm_failure_marks_sent_to_avoid_retry_spam() -> None:
    user = FakeUser(100, fail_send=True)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")
    now = datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc)

    await report._run_due_daily_reports_once(now=now, client=client)
    await report._run_due_daily_reports_once(now=now, client=client)

    assert len(client.calls) == 3
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"
    assert "Could not send Discord DM" in subscriptions[100]["last_error"]


async def test_partial_daily_dm_failure_is_recorded_without_retrying() -> None:
    user = FakeUser(100, fail_after=2)
    bot = FakeBot(users={100: user})
    client = FakeDailyStockSumClient()
    report = StockSumReport(bot=bot)
    await report._daily_store.set_subscription(user, time_utc="09:30")
    now = datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc)

    await report._run_due_daily_reports_once(now=now, client=client)
    await report._run_due_daily_reports_once(now=now, client=client)

    assert len(user.dm_messages) == 2
    assert len(client.calls) == 3
    subscriptions = await report._daily_store.all_subscriptions()
    assert subscriptions[100]["last_sent_utc_date"] == "2026-07-07"
    assert "Could not send Discord DM" in subscriptions[100]["last_error"]


def test_daily_renderer_keeps_ptr_last_and_preserves_every_disclosure_row() -> None:
    rows = [
        {
            "name": f"Filer {index}",
            "stock_ticker": f"T{index:03d}",
            "transaction_type": "Purchase",
            "transaction_date": "2026-07-01",
            "filing_date": "2026-07-07",
            "amount": "$1,001 - $15,000",
            "asset": f"Company {index} common stock",
            "pdf_url": f"https://example.test/{index}.pdf",
        }
        for index in range(100)
    ]
    sections = _daily_renderer_sections(ptr_rows=rows)

    messages = _format_daily_report(
        sections,
        sent_utc_date="2026-07-07",
        generated_at=datetime(2026, 7, 7, 9, 5, tzinfo=timezone.utc),
    )

    assert messages[0].startswith("**Stock-Sum Daily Brief**")
    assert "PTR" not in messages[0]
    assert "Disclosure" not in messages[0]
    ptr_index = next(index for index, message in enumerate(messages) if message.startswith("**House PTR Disclosures**"))
    assert all(message.startswith("**House PTR Disclosures") for message in messages[ptr_index:])
    ptr_text = "\n".join(messages[ptr_index:])
    assert "New filings: `100` · disclosure rows: `100`" in ptr_text
    for index in range(100):
        assert f"**T{index:03d}**" in ptr_text
        assert f"[PDF](https://example.test/{index}.pdf)" in ptr_text
    assert "**House PTR Disclosures (continued)**" in ptr_text
    assert all(len(message) <= DAILY_MESSAGE_LIMIT for message in messages)


def test_daily_renderer_displays_photo_scanned_filing_and_link() -> None:
    url = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/9116211.pdf"
    section = DailyReportSection(
        kind="trading",
        title="House PTR Disclosures",
        payload={
            "house_ptr": [],
            "house_ptr_filings": [
                {
                    "doc_id": "9116211",
                    "name": "Photo Filer",
                    "filing_date": "2026-07-08",
                    "pdf_url": url,
                    "extraction_status": "photo_scanned",
                    "transaction_count": 0,
                }
            ],
            "filters": {
                "collected_days": 1,
                "collected_start": "2026-07-14T09:00:00+00:00",
                "collected_end": "2026-07-15T09:00:00+00:00",
            },
        },
    )

    rendered = "\n".join(
        _format_daily_report(
            [section],
            sent_utc_date="2026-07-15",
            generated_at=datetime(2026, 7, 15, 9, 5, tzinfo=timezone.utc),
        )
    )

    assert "The filing is photo scanned" in rendered
    assert f"[PDF]({url})" in rendered
    assert "No new House PTR filings" not in rendered


def test_daily_renderer_sorts_and_caps_high_priority_social_signals() -> None:
    posts = []
    confidences = ["low", "high", "medium", "high", "low", "high", "medium"]
    for index, confidence in enumerate(confidences):
        posts.append(
            {
                "title": f"Signal {index}",
                "post_summary": f"Summary {index}",
                "sentiment": "bullish",
                "importance": "high",
                "confidence": confidence,
                "tickers": [f"T{index}"],
                "urls": [f"https://example.test/signal/{index}"],
            }
        )
    sections = _daily_renderer_sections(social_posts=posts)

    messages = _format_daily_report(
        sections,
        sent_utc_date="2026-07-07",
        generated_at=datetime(2026, 7, 7, 9, 5, tzinfo=timezone.utc),
    )
    social_text = "\n".join(message for message in messages if message.startswith("**High-Priority Social Signals"))

    assert f"Showing `{DAILY_SOCIAL_DISPLAY_LIMIT}` of `7` high-priority signals." in social_text
    assert "2 additional high-priority signals were omitted" in social_text
    expected_titles = ["Signal 1", "Signal 3", "Signal 5", "Signal 2", "Signal 6"]
    positions = [social_text.index(title) for title in expected_titles]
    assert positions == sorted(positions)
    assert "Signal 0" not in social_text
    assert "Signal 4" not in social_text


def test_daily_renderer_reports_healthy_social_empty_state_and_coverage() -> None:
    posts = [
        {"title": "Medium", "importance": "medium", "confidence": "high"},
        {"title": "Low", "importance": "low", "confidence": "low"},
    ]
    sections = _daily_renderer_sections(social_posts=posts)

    messages = _format_daily_report(
        sections,
        sent_utc_date="2026-07-07",
        generated_at=datetime(2026, 7, 7, 9, 5, tzinfo=timezone.utc),
    )
    social_text = "\n".join(message for message in messages if message.startswith("**High-Priority Social Signals"))

    assert "Configured source lookback: X `24h`" in social_text
    assert "No high-priority social signals were found" in social_text
    assert "`2` medium/low signals were not included" in social_text
    assert "Coverage: `3/3 healthy`" in messages[0]


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
        filing_days=1,
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
            "json": {
                "name": "Pelosi",
                "days": 30,
                "filing_days": 1,
                "asset_type": "ST",
                "ticker": "AMZN",
                "limit": 25,
                "force_refresh": True,
            },
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

    await client.run_trading_report(output_format="discord", filing_days=1)

    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trading-reports/jobs/discord",
        {
            "headers": {},
            "json": {"filing_days": 1, "force_refresh": False},
        },
    )


async def test_client_sends_allow_empty_only_when_enabled() -> None:
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "trade-empty"})],
        get_responses=[
            FakeResponse(200, {"job_id": "trade-empty", "status": "succeeded"}),
            FakeResponse(200, body=b"{}", headers={"content-type": "application/json"}),
        ],
    )
    client = StockSumHttpClient(base_url="http://stock-sum.local", session=session, poll_seconds=0)

    await client.run_trading_report(output_format="json", filing_days=1, allow_empty=True)

    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trading-reports/jobs/json",
        {
            "headers": {},
            "json": {"filing_days": 1, "force_refresh": False, "allow_empty": True},
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
        days=14,
        comparison_days=9,
        mentions_change_pct=40.5,
        sentiment_change_pct=25.0,
        minimum_mentions=80,
    )

    assert artifact.filename == "stock-sum-report-trend-1.md"
    assert session.requests[0] == (
        "POST",
        "http://stock-sum.local/v1/trendings/jobs/discord",
        {
            "headers": {},
            "json": {
                "from": "2026-07-01",
                "to": "2026-07-06",
                "limit": 3,
                "days": 14,
                "comparison_days": 9,
                "mentions_change_pct": 40.5,
                "sentiment_change_pct": 25.0,
                "minimum_mentions": 80,
            },
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


async def test_client_preserves_xpoz_usage_limit_job_error() -> None:
    error = (
        "Collection failed with no usable source data. Xpoz usage limit exceeded. "
        "The configured Xpoz account has no remaining credits. "
        "Upgrade the plan or add credits at https://xpoz.ai/usage, then retry. "
        "Failed collectors: x.aleabitoreddit, reddit.wallstreetbets."
    )
    session = FakeSession(
        post_responses=[FakeResponse(202, {"job_id": "job-xpoz-limit"})],
        get_responses=[FakeResponse(200, {"job_id": "job-xpoz-limit", "status": "failed", "error": error})],
    )
    client = StockSumHttpClient(session=session, poll_seconds=0)

    with pytest.raises(StockSumRequestError) as exc_info:
        await client.run_social_report(output_format="discord")

    assert error in str(exc_info.value)


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
        "redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=("first paragraph\n\n" + ("x" * 1950)).encode("utf-8")),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
        "redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env",
        lambda: client,
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
        "redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env",
        lambda: FakeFailingStockSumClient(),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.recent_posts(interaction)

    assert interaction.response.messages[0]["content"] == "Social report is being generated, please wait a few minutes."
    assert interaction.channel.messages == [
        {
            "content": "stock-sum report failed: broken",
            "suppress_embeds": True,
        }
    ]


async def test_report_command_renders_xpoz_usage_limit_failure(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    error = (
        "Collection failed with no usable source data. Xpoz usage limit exceeded. "
        "The configured Xpoz account has no remaining credits. "
        "Upgrade the plan or add credits at https://xpoz.ai/usage, then retry. "
        "Failed collectors: x.aleabitoreddit, reddit.wallstreetbets."
    )
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env",
        lambda: FakeFailingStockSumClient(error),
    )
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.recent_posts(interaction)

    assert interaction.channel.messages == [
        {
            "content": f"stock-sum report failed: {error}",
            "suppress_embeds": True,
        }
    ]


async def test_trendings_command_sends_public_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"**Trending stocks**\n- NVDA")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.trendings(
        interaction,
        limit=3,
        comparison_days=9,
        mentions_change_pct=40.0,
        sentiment_change_pct=25.0,
        minimum_mentions=80,
    )

    assert client.trendings_calls == [
        {
            "output_format": "discord",
            "limit": 3,
            "days": 1,
            "comparison_days": 9,
            "mentions_change_pct": 40.0,
            "sentiment_change_pct": 25.0,
            "minimum_mentions": 80,
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


async def test_trendings_command_rejects_invalid_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.trendings(interaction, limit=0)

    assert client.trendings_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: limit must be 1 or greater.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_trendings_command_rejects_invalid_comparison_days(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.trendings(interaction, comparison_days=0)

    assert client.trendings_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: comparison_days must be 1 or greater.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_trendings_command_rejects_invalid_change_thresholds(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.trendings(interaction, mentions_change_pct=0)

    assert client.trendings_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: mentions_change_pct must be greater than 0.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_ptr_search_command_rejects_missing_filters(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    monkeypatch.setattr(
        "redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env",
        lambda: FakeStockSumClient(content=b"unused"),
    )

    await report.ptr_search(interaction)

    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: ptr_search requires at least one filter: name, collected days, transaction dates, filing dates, asset_type, or ticker.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]
    assert interaction.followup.messages == []


async def test_ptr_search_rejects_invalid_date_and_limit_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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


async def test_ptr_search_rejects_invalid_filing_date_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.ptr_search(interaction, filing_start_date="bad-date")

    assert client.trading_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: filing_start_date must be in YYYY-MM-DD format.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_ptr_search_days_uses_collected_at_and_combines_with_transaction_dates(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"trade")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.ptr_search(
        interaction,
        days=7,
        start_date="2026-07-01",
        end_date="2026-07-08",
    )

    assert client.trading_calls[0]["days"] is None
    assert client.trading_calls[0]["collected_days"] == 7
    assert client.trading_calls[0]["start_date"] == "2026-07-01"
    assert client.trading_calls[0]["end_date"] == "2026-07-08"


async def test_ptr_search_rejects_mixed_filing_days_and_dates(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.ptr_search(interaction, filing_days=1, filing_start_date="2026-07-01")

    assert client.trading_calls == []
    assert interaction.response.messages == [
        {
            "content": "stock-sum report failed: Use either filing_days or filing start/end dates, not both.",
            "ephemeral": True,
            "suppress_embeds": True,
        }
    ]


async def test_ptr_search_rejects_unknown_asset_type_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.ptr_search(interaction, asset_type="bad!")

    assert client.trading_calls == []
    assert "asset_type must be" in interaction.response.messages[0]["content"]


async def test_ptr_search_command_sends_discord_report(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=("trade one\n\ntrade two").encode("utf-8"))
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.ptr_search(
        interaction,
        name="Pelosi",
        days=30,
        filing_days=1,
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
            "days": None,
            "collected_days": 30,
            "filing_start_date": None,
            "filing_end_date": None,
            "filing_days": 1,
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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.ptr_search(interaction, filing_days=1)

    assert client.trading_calls[0]["output_format"] == "discord"
    assert client.trading_calls[0]["limit"] is None
    assert client.trading_calls[0]["filing_days"] == 1


async def test_ptr_search_command_does_not_clip_large_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"trade")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.ptr_search(interaction, days=30, limit=5000)

    assert client.trading_calls[0]["output_format"] == "discord"
    assert client.trading_calls[0]["days"] is None
    assert client.trading_calls[0]["collected_days"] == 30
    assert client.trading_calls[0]["limit"] == 5000


async def test_13f_search_rejects_invalid_filters_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.thirteenf_search(interaction, issuer="NVIDIA")

    assert client.sec_13f_calls[0]["output_format"] == "discord"
    assert client.sec_13f_calls[0]["limit"] is None


async def test_13f_search_command_does_not_clip_large_limit(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"holding")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.thirteenf_search(interaction, issuer="NVIDIA", limit=5000)

    assert client.sec_13f_calls[0]["output_format"] == "discord"
    assert client.sec_13f_calls[0]["limit"] == 5000


async def test_plot_rejects_invalid_parameters_before_api_call(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=None)
    client = FakeStockSumClient(content=b"unused")
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.discord", FakeDiscord)

    await report.plot(interaction, mode="trading", fuzzy_search="openai", days=30)

    assert "Select a fuzzy_search match" in interaction.channel.messages[0]["content"]
    assert interaction.channel.sent_messages[0].reactions == ["1️⃣"]
    assert client.statistic_calls[0]["asset_name"] == "OpenAI Global LLC [OI]"


async def test_settings_command_blocks_non_owner(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=False))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

    await report.settings_add_x(interaction, handle="bad/handle")

    assert client.calls == []
    assert "X handle must be" in interaction.response.messages[0]["content"]


async def test_settings_reddit_source_add_uses_endpoint_defaults(monkeypatch) -> None:
    interaction = FakeInteraction()
    report = StockSumReport(bot=FakeBot(owner=True))
    client = FakeManagementClient()
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
    monkeypatch.setattr("redbot_cogs.stocksum_report.cog.StockSumHttpClient.from_env", lambda: client)

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
        filing_start_date: str | None = None,
        filing_end_date: str | None = None,
        filing_days: int | None = None,
        collected_days: int | None = None,
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
                "filing_start_date": filing_start_date,
                "filing_end_date": filing_end_date,
                "filing_days": filing_days,
                "collected_days": collected_days,
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
    def __init__(self, message: str = "broken") -> None:
        self.message = message

    async def run_social_report(self, *, output_format: str, detail: str = "minimum") -> StockSumArtifact:
        raise StockSumRequestError(self.message)

    async def run_trading_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError(self.message)

    async def run_13f_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError(self.message)

    async def run_statistic(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError(self.message)

    async def run_trendings_report(self, **kwargs: Any) -> StockSumArtifact:
        raise StockSumRequestError(self.message)


class FakeDailyStockSumClient:
    def __init__(self, *, fail_methods: set[str] | None = None) -> None:
        self.fail_methods = fail_methods or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def run_trendings_report(self, **kwargs: Any) -> StockSumArtifact:
        self.calls.append(("trendings", kwargs))
        if "trendings" in self.fail_methods:
            raise StockSumRequestError("trendings broken")
        return _daily_artifact(
            {
                "report_type": "trendings",
                "summary": {
                    "changes": [
                        {
                            "platform": "reddit",
                            "ticker": "NVDA",
                            "company_name": "NVIDIA",
                            "change_type": "mentions + sentiment",
                            "previous_mentions": 42,
                            "current_mentions": 84,
                            "mentions_delta_pct": 100.0,
                            "bullish_delta_points": 35,
                            "bearish_delta_points": -33,
                        }
                    ],
                    "stocks": [
                        {
                            "platform": "reddit",
                            "ticker": "NVDA",
                            "company_name": "NVIDIA",
                            "trend": "up",
                            "mentions": 84,
                            "bullish_pct": 65,
                            "bearish_pct": 12,
                        }
                    ],
                    "sectors": [],
                },
                "filters": {
                    "from": "2026-07-07",
                    "to": "2026-07-07",
                    "display_limit": 5,
                    "comparison_days": 7,
                },
            }
        )

    async def run_social_report(self, **kwargs: Any) -> StockSumArtifact:
        self.calls.append(("social", kwargs))
        if "social" in self.fail_methods:
            raise StockSumRequestError("social broken")
        return _daily_artifact(
            {
                "report_type": "social",
                "generated_at": "2026-07-07T09:00:00+00:00",
                "source_windows": {
                    "x": {
                        "marketwatcher": {
                            "window_start": "2026-07-06T09:00:00+00:00",
                            "lookback_hours": 24,
                        }
                    },
                    "reddit": {},
                },
                "summary": {
                    "x_reports": [
                        {
                            "handle": "marketwatcher",
                            "posts": [
                                {
                                    "title": "NVDA momentum",
                                    "post_summary": "NVDA discussion accelerated.",
                                    "sentiment": "bullish",
                                    "importance": "high",
                                    "confidence": "high",
                                    "interpretation": "Momentum is broadening.",
                                    "tickers": ["NVDA"],
                                    "urls": ["https://x.com/marketwatcher/status/1"],
                                }
                            ],
                        }
                    ],
                    "reddit_report": {"posts": []},
                },
            }
        )

    async def run_trading_report(self, **kwargs: Any) -> StockSumArtifact:
        self.calls.append(("trading", kwargs))
        if "trading" in self.fail_methods:
            raise StockSumRequestError("trading broken")
        return _daily_artifact(
            {
                "report_type": "trading",
                "house_ptr": [
                    {
                        "name": "Jane Doe",
                        "status": "Member",
                        "state": "CA",
                        "filing_date": "2026-07-07",
                        "asset": "Apple Inc. - Common Stock",
                        "stock_ticker": "AAPL",
                        "transaction_type": "Purchase",
                        "transaction_date": "2026-07-01",
                        "amount": "$1,001 - $15,000",
                        "pdf_url": "https://example.test/ptr.pdf",
                    }
                ],
                "filters": {
                    "filing_days": 1,
                    "filing_start": "2026-07-06T09:00:00+00:00",
                    "filing_end": "2026-07-07T09:00:00+00:00",
                    "limit": 100,
                    "allow_empty": True,
                },
            }
        )


def _daily_artifact(payload: dict[str, Any], *, status: dict[str, Any] | None = None) -> StockSumArtifact:
    return StockSumArtifact(
        job_id="daily",
        filename="daily.json",
        content_type="application/json",
        content=json.dumps(payload).encode("utf-8"),
        status=status or {"status": "succeeded"},
    )


def _daily_renderer_sections(
    *,
    social_posts: list[dict[str, Any]] | None = None,
    ptr_rows: list[dict[str, Any]] | None = None,
) -> list[DailyReportSection]:
    return [
        DailyReportSection(
            kind="trendings",
            title="Market Trends",
            payload={
                "summary": {
                    "changes": [
                        {
                            "platform": "reddit",
                            "ticker": "NVDA",
                            "change_type": "mentions",
                            "previous_mentions": 10,
                            "current_mentions": 20,
                            "mentions_delta_pct": 100.0,
                        }
                    ],
                    "stocks": [],
                    "sectors": [],
                },
                "filters": {
                    "from": "2026-07-07",
                    "to": "2026-07-07",
                    "display_limit": 5,
                    "comparison_days": 7,
                },
            },
        ),
        DailyReportSection(
            kind="social",
            title="High-Priority Social Signals",
            payload={
                "source_windows": {
                    "x": {
                        "marketwatcher": {
                            "window_start": "2026-07-06T09:00:00+00:00",
                            "lookback_hours": 24,
                        }
                    },
                    "reddit": {},
                },
                "summary": {
                    "x_reports": [{"handle": "marketwatcher", "posts": social_posts or []}],
                    "reddit_report": {"posts": []},
                },
            },
        ),
        DailyReportSection(
            kind="trading",
            title="House PTR Disclosures",
            payload={
                "house_ptr": ptr_rows or [],
                "filters": {
                    "filing_days": 1,
                    "filing_start": "2026-07-06T09:00:00+00:00",
                    "filing_end": "2026-07-07T09:00:00+00:00",
                    "limit": 100,
                    "allow_empty": True,
                },
            },
        ),
    ]


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponseSender()
        self.followup = FakeFollowupSender()
        self.channel = FakeChannelSender()
        self.user = FakeUser(100)


class StrictFileNoneInteraction:
    def __init__(self, *, channel: Any | None = object()) -> None:
        self.response = StrictFileNoneSender()
        self.followup = StrictFileNoneSender()
        self.channel = channel


class StrictFileNoneSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def is_done(self) -> bool:
        return False

    async def send_message(self, content: str, **kwargs: Any) -> None:
        if kwargs.get("file") is None and "file" in kwargs:
            raise AssertionError("file=None must not be sent to discord.py")
        self.calls.append((content, kwargs))

    async def send(self, content: str, **kwargs: Any) -> None:
        if kwargs.get("file") is None and "file" in kwargs:
            raise AssertionError("file=None must not be sent to discord.py")
        self.calls.append((content, kwargs))


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
    def __init__(
        self,
        *,
        owner: bool = True,
        reaction_emoji: str = "1️⃣",
        reaction_user_id: int = 100,
        timeout: bool = False,
        users: dict[int, "FakeUser"] | None = None,
    ) -> None:
        self.owner = owner
        self.reaction_emoji = reaction_emoji
        self.reaction_user_id = reaction_user_id
        self.timeout = timeout
        self.users = users or {}

    async def is_owner(self, _user: object) -> bool:
        return self.owner

    def get_user(self, user_id: int) -> "FakeUser" | None:
        return self.users.get(user_id)

    async def fetch_user(self, user_id: int) -> "FakeUser" | None:
        return self.users.get(user_id)

    async def wait_for(self, _event: str, *, timeout: float, check: Any) -> Any:
        if self.timeout:
            raise asyncio.TimeoutError
        message_id = FakeMessage._last_id
        reaction = FakeRawReaction(self.reaction_emoji, message_id=message_id, user_id=self.reaction_user_id)
        if check(reaction):
            return reaction
        raise asyncio.TimeoutError


class FakeUser:
    def __init__(self, user_id: int, *, fail_send: bool = False, fail_after: int | None = None) -> None:
        self.id = user_id
        self.fail_send = fail_send
        self.fail_after = fail_after
        self.dm_messages: list[dict[str, Any]] = []

    async def send(self, content: str, *, suppress_embeds: bool = False) -> None:
        if self.fail_send or (self.fail_after is not None and len(self.dm_messages) >= self.fail_after):
            raise RuntimeError("DMs closed")
        self.dm_messages.append({"content": content, "suppress_embeds": suppress_embeds})


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
