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
            source_type="x_user_timeline",
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
    assert "x" in result.output


def test_collect_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["collect", "--help"])

    assert result.exit_code == 0
    assert "--collector" in result.output
    assert "--profile" in result.output


def test_collect_collector_uses_pipeline(monkeypatch) -> None:
    import stock_sum.cli as cli

    monkeypatch.setattr(cli, "ReportPipeline", FakeCollectPipeline)
    runner = CliRunner()
    result = runner.invoke(app, ["collect", "--collector", "x.market_watch"])

    assert result.exit_code == 0
    assert '"collector_id": "x.market_watch"' in result.output
    assert '"inserted_count": 1' in result.output


def test_x_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["x", "--help"])

    assert result.exit_code == 0
    assert "scrape" in result.output
    assert "login" in result.output
    assert "status" in result.output
    assert "diagnose" in result.output


def test_x_scrape_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["x", "scrape", "--help"])

    assert result.exit_code == 0
    assert "--handle" in result.output
    assert "--limit" in result.output
    assert "--channel" in result.output


def test_x_login_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["x", "login", "--help"])

    assert result.exit_code == 0
    assert "--channel" in result.output
    assert "--wait-seconds" in result.output
