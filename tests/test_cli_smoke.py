"""CLI smoke tests."""

from typer.testing import CliRunner

from stock_sum.core.models import CollectionRunResult
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


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run-report" in result.output
    assert "collect" in result.output
    assert "config" in result.output
    assert " reddit " not in result.output
    assert " x " not in result.output


def test_collect_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["collect", "--help"])

    assert result.exit_code == 0
    assert "--collector" in result.output
    assert "--profile" in result.output


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
