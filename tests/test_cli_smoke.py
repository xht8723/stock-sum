"""CLI smoke tests."""

from typer.testing import CliRunner

from stock_sum.cli import app


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run-report" in result.output
    assert "config" in result.output
    assert "x" in result.output


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
