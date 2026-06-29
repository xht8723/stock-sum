"""CLI smoke tests."""

from typer.testing import CliRunner

from stock_sum.core.models import CollectionRunResult
from stock_sum.core.models import Summary
from stock_sum.cli import app


class FakeCollectPipeline:
    def __init__(self, context):
        self.context = context

    async def collect_collector(self, collector_id: str):
        return CollectionRunResult(
            run_id="run-1",
            collector_id=collector_id,
            source_type="test_source",
            status="succeeded",
            collected_count=1,
            inserted_count=1,
            updated_count=0,
            sqlite_path=":memory:",
        )


class FakePayload:
    def to_dict(self, *, mode="full", max_images_per_post=3, max_images_total=20):
        return {
            "profile": "default",
            "mode": mode,
            "max_images_per_post": max_images_per_post,
            "max_images_total": max_images_total,
        }


class FakePayloadBuilder:
    def __init__(self, *, config, repository, downloader=None):
        self.config = config
        self.repository = repository
        self.downloader = downloader

    async def build(self, *, profile: str, download_images: bool | None = None):
        return FakePayload()


class FakeLLMClient:
    provider = "deepseek"
    model = "deepseek-v4-flash"

    async def summarize(self, payload, instructions=None):
        return Summary(
            text='{"executive_summary":["ok"],"metadata":{"not_financial_advice":true}}',
            model=self.model,
            metadata={
                "provider": self.provider,
                "parsed": {"executive_summary": ["ok"], "metadata": {"not_financial_advice": True}},
                "usage": {"prompt_tokens": 1},
            },
        )


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run-report" in result.output
    assert "collect" in result.output
    assert "config" in result.output
    assert "setup" in result.output
    assert "secrets" in result.output
    assert "payload" in result.output
    assert "llm" in result.output
    assert "report" in result.output
    assert " reddit " not in result.output
    assert " x " not in result.output


def test_collect_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["collect", "--help"])

    assert result.exit_code == 0
    assert "--collector" in result.output
    assert "--profile" in result.output


def test_payload_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["payload", "--help"])

    assert result.exit_code == 0
    assert "build" in result.output


def test_llm_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["llm", "--help"])

    assert result.exit_code == 0
    assert "summarize" in result.output


def test_report_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["report", "--help"])

    assert result.exit_code == 0
    assert "render" in result.output


def test_config_profile_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["config", "profile", "--help"])

    assert result.exit_code == 0
    assert "add" in result.output
    assert "edit" in result.output
    assert "delete" in result.output


def test_config_source_help() -> None:
    runner = CliRunner()

    x_result = runner.invoke(app, ["config", "x-user", "--help"])
    reddit_result = runner.invoke(app, ["config", "subreddit", "--help"])

    assert x_result.exit_code == 0
    assert "add" in x_result.output
    assert "delete" in x_result.output
    assert reddit_result.exit_code == 0
    assert "add" in reddit_result.output
    assert "delete" in reddit_result.output


def test_llm_providers_lists_deepseek() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["llm", "providers"])

    assert result.exit_code == 0
    assert '"provider": "deepseek"' in result.output
    assert '"api_key_env": "DEEPSEEK_API_KEY"' in result.output
    assert '"implemented": true' in result.output


def test_secrets_set_list_remove_without_printing_value(tmp_path) -> None:
    env_file = tmp_path / ".env"
    runner = CliRunner()

    set_result = runner.invoke(
        app,
        ["secrets", "set", "TEST_SECRET", "--env-file", str(env_file), "--value", "super-secret"],
    )
    env_text_after_set = env_file.read_text(encoding="utf-8")
    list_result = runner.invoke(app, ["secrets", "list", "--env-file", str(env_file)])
    remove_result = runner.invoke(app, ["secrets", "remove", "TEST_SECRET", "--env-file", str(env_file)])
    list_after_remove = runner.invoke(app, ["secrets", "list", "--env-file", str(env_file)])

    assert set_result.exit_code == 0
    assert "super-secret" not in set_result.output
    assert env_text_after_set == "TEST_SECRET=super-secret\n"
    assert list_result.exit_code == 0
    assert "TEST_SECRET" in list_result.output
    assert "super-secret" not in list_result.output
    assert remove_result.exit_code == 0
    assert list_after_remove.exit_code == 0
    assert "TEST_SECRET" not in list_after_remove.output


def test_setup_init_yes_writes_config_env_and_sources(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "init",
            "--yes",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
            "--xpoz-api-key",
            "xpoz-secret",
            "--llm-api-key",
            "deepseek-secret",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--x-user",
            "aleabitoreddit",
            "--subreddit",
            "wallstreetbets",
        ],
    )

    assert result.exit_code == 0
    config_text = config_path.read_text(encoding="utf-8")
    env_text = env_file.read_text(encoding="utf-8")
    assert 'host = "0.0.0.0"' in config_text
    assert "port = 8080" in config_text
    assert 'provider = "deepseek"' in config_text
    assert '"x.aleabitoreddit"' in config_text
    assert '"reddit.wallstreetbets"' in config_text
    assert "XPOZ_API_KEY=xpoz-secret" in env_text
    assert "DEEPSEEK_API_KEY=deepseek-secret" in env_text
    assert "deepseek-secret" not in result.output
    assert "Next steps" in result.output


def test_setup_check_reports_missing_and_present_secrets(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    runner = CliRunner()

    init_result = runner.invoke(app, ["config", "init", str(config_path)])
    set_xpoz_env = runner.invoke(
        app,
        ["config", "set", str(config_path), "providers.xpoz.api_key_env", "'MISSING_TEST_XPOZ_KEY'"],
    )
    set_llm_env = runner.invoke(
        app,
        ["config", "set", str(config_path), "llm.api_key_env", "'MISSING_TEST_DEEPSEEK_KEY'"],
    )
    missing_result = runner.invoke(app, ["setup", "check", "--config", str(config_path), "--env-file", str(env_file)])
    set_xpoz = runner.invoke(app, ["secrets", "set", "MISSING_TEST_XPOZ_KEY", "--env-file", str(env_file), "--value", "x"])
    set_llm = runner.invoke(app, ["secrets", "set", "MISSING_TEST_DEEPSEEK_KEY", "--env-file", str(env_file), "--value", "d"])
    passed_result = runner.invoke(app, ["setup", "check", "--config", str(config_path), "--env-file", str(env_file)])

    assert init_result.exit_code == 0
    assert set_xpoz_env.exit_code == 0
    assert set_llm_env.exit_code == 0
    assert missing_result.exit_code == 1
    assert "Missing required secrets" in missing_result.output
    assert set_xpoz.exit_code == 0
    assert set_llm.exit_code == 0
    assert passed_result.exit_code == 0
    assert "Setup check passed" in passed_result.output


def test_setup_reset_requires_double_confirmation_and_removes_targets(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "stock_sum.sqlite3").write_text("db", encoding="utf-8")
    config_path.write_text("[service]\nname = \"stock-sum\"\n", encoding="utf-8")
    env_file.write_text("XPOZ_API_KEY=secret\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "reset",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
            "--data-dir",
            str(data_dir),
        ],
        input="y\nRESET\n",
    )

    assert result.exit_code == 0
    assert not config_path.exists()
    assert not env_file.exists()
    assert not data_dir.exists()
    assert '"status": "reset"' in result.output
    assert "stock-sum setup init" in result.output


def test_setup_reset_cancel_keeps_targets(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_path.write_text("[service]\nname = \"stock-sum\"\n", encoding="utf-8")
    env_file.write_text("XPOZ_API_KEY=secret\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "setup",
            "reset",
            "--config",
            str(config_path),
            "--env-file",
            str(env_file),
            "--data-dir",
            str(data_dir),
        ],
        input="n\n",
    )

    assert result.exit_code == 1
    assert config_path.exists()
    assert env_file.exists()
    assert data_dir.exists()
    assert "Reset cancelled" in result.output


def test_daemon_reports_missing_setup_without_starting_server(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()
    init_result = runner.invoke(app, ["config", "init", str(config_path)])
    set_result = runner.invoke(
        app,
        ["config", "set", str(config_path), "llm.api_key_env", "'MISSING_TEST_LLM_KEY'"],
    )
    monkeypatch.delenv("MISSING_TEST_LLM_KEY", raising=False)

    result = runner.invoke(app, ["daemon", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert set_result.exit_code == 0
    assert result.exit_code == 1
    assert "stock-sum" in result.output
    assert "setup init" in result.output


def test_config_profile_add_edit_delete(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()

    init_result = runner.invoke(app, ["config", "init", str(config_path)])
    add_result = runner.invoke(
        app,
        [
            "config",
            "profile",
            "add",
            "closing",
            "--config",
            str(config_path),
            "--timezone",
            "America/Vancouver",
            "--schedule",
            "0 16 * * 1-5",
            "--collectors",
            "api.market_watch",
            "--deliveries",
            "email.primary",
        ],
    )
    edit_result = runner.invoke(
        app,
        [
            "config",
            "profile",
            "edit",
            "closing",
            "--config",
            str(config_path),
            "--collectors",
            "api.market_watch,api.news",
        ],
    )
    show_result = runner.invoke(app, ["config", "profile", "show", "closing", "--config", str(config_path)])
    delete_result = runner.invoke(app, ["config", "profile", "delete", "closing", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert add_result.exit_code == 0
    assert edit_result.exit_code == 0
    assert show_result.exit_code == 0
    assert '"api.market_watch"' in show_result.output
    assert '"api.news"' in show_result.output
    assert delete_result.exit_code == 0


def test_config_x_user_add_list_delete_updates_profile(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()

    init_result = runner.invoke(app, ["config", "init", str(config_path)])
    add_result = runner.invoke(
        app,
        [
            "config",
            "x-user",
            "add",
            "newhandle",
            "--config",
            str(config_path),
            "--limit",
            "20",
            "--profile",
            "default",
        ],
    )
    list_result = runner.invoke(app, ["config", "x-user", "list", "--config", str(config_path)])
    profile_result = runner.invoke(app, ["config", "profile", "show", "default", "--config", str(config_path)])
    delete_result = runner.invoke(
        app,
        ["config", "x-user", "delete", "newhandle", "--config", str(config_path), "--profile", "default"],
    )
    profile_after_delete = runner.invoke(app, ["config", "profile", "show", "default", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert add_result.exit_code == 0
    assert list_result.exit_code == 0
    assert '"handle": "newhandle"' in list_result.output
    assert '"x.newhandle"' in profile_result.output
    assert delete_result.exit_code == 0
    assert '"x.newhandle"' not in profile_after_delete.output


def test_config_subreddit_add_list_delete_updates_profile(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    runner = CliRunner()

    init_result = runner.invoke(app, ["config", "init", str(config_path)])
    add_result = runner.invoke(
        app,
        [
            "config",
            "subreddit",
            "add",
            "r/stocks",
            "--config",
            str(config_path),
            "--sort",
            "top",
            "--timeframe",
            "week",
            "--include-comments",
            "--comments-per-post",
            "3",
            "--profile",
            "default",
        ],
    )
    list_result = runner.invoke(app, ["config", "subreddit", "list", "--config", str(config_path)])
    profile_result = runner.invoke(app, ["config", "profile", "show", "default", "--config", str(config_path)])
    delete_result = runner.invoke(
        app,
        ["config", "subreddit", "delete", "stocks", "--config", str(config_path), "--profile", "default"],
    )
    profile_after_delete = runner.invoke(app, ["config", "profile", "show", "default", "--config", str(config_path)])

    assert init_result.exit_code == 0
    assert add_result.exit_code == 0
    assert list_result.exit_code == 0
    assert '"subreddit": "stocks"' in list_result.output
    assert '"include_comments": true' in list_result.output
    assert '"reddit.stocks"' in profile_result.output
    assert delete_result.exit_code == 0
    assert '"reddit.stocks"' not in profile_after_delete.output


def test_collect_collector_uses_pipeline(monkeypatch) -> None:
    import stock_sum.cli as cli

    monkeypatch.setattr(cli, "ReportPipeline", FakeCollectPipeline)
    runner = CliRunner()
    result = runner.invoke(app, ["collect", "--collector", "api.market_watch"])

    assert result.exit_code == 0
    assert '"collector_id": "api.market_watch"' in result.output
    assert '"inserted_count": 1' in result.output


def test_payload_build_writes_json(monkeypatch, tmp_path) -> None:
    import stock_sum.cli as cli

    output = tmp_path / "payload.json"
    monkeypatch.setattr(cli, "SummaryInputBuilder", FakePayloadBuilder)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "payload",
            "build",
            "--profile",
            "default",
            "--output",
            str(output),
            "--config",
            "stock_sum/config/example.toml",
            "--mode",
            "compact",
            "--max-images-per-post",
            "2",
            "--max-images-total",
            "4",
        ],
    )

    assert result.exit_code == 0
    assert output.exists()
    assert '"profile": "default"' in output.read_text()
    assert '"mode": "compact"' in output.read_text()


def test_llm_summarize_writes_json_from_payload(monkeypatch, tmp_path) -> None:
    import stock_sum.cli as cli

    payload = tmp_path / "payload.json"
    payload.write_text('{"sources":{"x":[],"reddit":[]},"media":{"m1":{"source_ref":"x1","remote_url":"https://cdn.example/1.jpg"}}}', encoding="utf-8")
    output = tmp_path / "summary.json"
    monkeypatch.setattr(cli, "build_llm_client", lambda config: FakeLLMClient())

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "llm",
            "summarize",
            "--profile",
            "default",
            "--payload",
            str(payload),
            "--output",
            str(output),
            "--config",
            "stock_sum/config/example.toml",
        ],
    )

    assert result.exit_code == 0
    assert output.exists()
    text = output.read_text(encoding="utf-8")
    assert '"provider": "deepseek"' in text
    assert '"model": "deepseek-v4-flash"' in text
    assert '"executive_summary"' in text
    assert '"input_media"' in text
    assert '"https://cdn.example/1.jpg"' in text


def test_report_render_writes_all_modes(tmp_path) -> None:
    response = tmp_path / "response.json"
    response.write_text(
        """
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "summary": {
    "executive_summary": ["ok"],
    "x_signals": [{"source_ref": "x1", "claim": "x claim", "confidence": "low", "urls": ["https://x.com/a/status/1"]}],
    "reddit_signals": [{"source_ref": "r1", "claim": "reddit claim", "confidence": "medium", "urls": ["https://reddit.com/r/test/comments/1"]}],
    "media_observations": [{"media_id": "m1", "source_ref": "r1", "observation": "image"}],
    "risks_or_uncertainties": ["risk"],
    "notable_sources": [{"source_ref": "r1", "url": "https://reddit.com/r/test/comments/1", "reason": "notable"}],
    "metadata": {"not_financial_advice": true}
  }
}
""",
        encoding="utf-8",
    )
    runner = CliRunner()

    for mode, expected in [("html", "<!doctype html>"), ("markdown", "# Market Social Digest"), ("text", "MARKET SOCIAL DIGEST")]:
        output = tmp_path / f"report.{mode}"
        result = runner.invoke(
            app,
            ["report", "render", "--input", str(response), "--output", str(output), "--mode", mode],
        )
        assert result.exit_code == 0
        assert output.exists()
        assert expected in output.read_text(encoding="utf-8")


def test_report_render_can_include_capitol_trades(monkeypatch, tmp_path) -> None:
    response = tmp_path / "response.json"
    response.write_text('{"summary":{"x_reports":[],"reddit_report":{"posts":[]}}}', encoding="utf-8")
    output = tmp_path / "report.html"

    class FakeSnapshot:
        def to_dict(self):
            return {
                "source_url": "https://www.capitoltrades.com/trades?page=1",
                "cards": [{"label": "TRADES", "value": "36,776"}],
                "trades": [
                    {
                        "politician": "Nancy Pelosi",
                        "party": "Democrat",
                        "chamber": "House",
                        "state": "CA",
                        "issuer": "Intel Corp",
                        "ticker": "INTC:US",
                        "published": "24 Jun 2026",
                        "traded": "28 May 2026",
                        "filed_after": "25 days",
                        "owner": "Spouse",
                        "transaction_type": "BUY*",
                        "size": "1M-5M",
                        "price": "N/A",
                    }
                ],
            }

    async def fake_scrape(**kwargs):
        return FakeSnapshot()

    monkeypatch.setattr("stock_sum.cli.scrape_capitol_trades", fake_scrape)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "report",
            "render",
            "--input",
            str(response),
            "--output",
            str(output),
            "--mode",
            "html",
            "--include-capitol-trades",
        ],
    )

    assert result.exit_code == 0
    rendered = output.read_text(encoding="utf-8")
    assert "Politician Trading Info" in rendered
    assert "Nancy Pelosi" in rendered
    assert "BUY*" in rendered


def test_report_render_rejects_invalid_mode(tmp_path) -> None:
    response = tmp_path / "response.json"
    response.write_text('{"summary":{"executive_summary":["ok"]}}', encoding="utf-8")
    output = tmp_path / "report.pdf"
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["report", "render", "--input", str(response), "--output", str(output), "--mode", "pdf"],
    )

    assert result.exit_code == 1
    assert "Unsupported presentation mode" in result.output
