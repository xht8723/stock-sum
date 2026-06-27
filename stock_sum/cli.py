"""Command line interface for stock-sum."""

from __future__ import annotations

from datetime import timedelta
from dataclasses import asdict
from pathlib import Path
from typing import Any
import asyncio
import ast
import json

import typer
import uvicorn
from rich.console import Console

from stock_sum.config.loader import load_config
from stock_sum.config.writer import get_dotted_value, read_toml_document, set_dotted_value, write_default_config
from stock_sum.collectors.playwright.x import (
    XAuthenticationRequired,
    XScrapeError,
    XUserCollector,
    diagnose_x_profile,
    login_to_x,
    x_profile_status,
)
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import RawItem
from stock_sum.core.pipeline import ReportPipeline
from stock_sum.llm.catalog import load_models_dev_catalog
from stock_sum.service.daemon import build_daemon

app = typer.Typer(help="Trading information summarization service.")
config_app = typer.Typer(help="Manage TOML configuration.")
x_app = typer.Typer(help="Scrape and manage X.com browser sessions.")
app.add_typer(config_app, name="config")
app.add_typer(x_app, name="x")
console = Console()
VALID_BROWSER_CHANNELS = {"", "chrome", "msedge", "chromium"}


def _parse_value(raw: str) -> Any:
    try:
        return ast.literal_eval(raw)
    except Exception:
        return raw


def _raw_item_to_jsonable(item: RawItem) -> dict[str, Any]:
    data = asdict(item)
    data["collected_at"] = item.collected_at.isoformat()
    return data


def _validate_browser_channel(channel: str) -> str:
    if channel not in VALID_BROWSER_CHANNELS:
        raise typer.BadParameter("Channel must be one of: chrome, msedge, chromium.")
    return channel


@app.command("run-report")
def run_report(
    profile: str = typer.Option(..., "--profile", "-p", help="Report profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Manually request a report pipeline run."""

    settings = load_config(config)
    pipeline = ReportPipeline(RuntimeContext(config=settings))
    asyncio.run(pipeline.run_report(profile))


@app.command()
def daemon(
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port"),
) -> None:
    """Run the HTTP service and scheduler host."""

    settings = load_config(config)
    uvicorn.run(build_daemon(settings), host=host, port=port)


@config_app.command("init")
def config_init(
    path: Path = typer.Argument(Path("config.toml")),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    """Create a readable starter TOML config."""

    written = write_default_config(path, overwrite=overwrite)
    console.print(f"Wrote {written}")


@config_app.command("validate")
def config_validate(path: Path) -> None:
    """Validate a TOML config file."""

    load_config(path)
    console.print("Config is valid.")


@config_app.command("get")
def config_get(path: Path, key: str) -> None:
    """Get a dotted config value."""

    document = read_toml_document(path)
    console.print(get_dotted_value(document, key))


@config_app.command("set")
def config_set(path: Path, key: str, value: str) -> None:
    """Set a dotted config value."""

    set_dotted_value(path, key, _parse_value(value))
    console.print(f"Updated {key}")


@config_app.command("sync")
def config_sync(
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Force a models.dev refresh."),
) -> None:
    """Refresh cache-backed external configuration metadata."""

    settings = load_config(config)
    cache_entry = asyncio.run(
        load_models_dev_catalog(
            settings.models_dev.cache_path,
            source_url=settings.models_dev.api_url,
            refresh_interval=timedelta(hours=settings.models_dev.refresh_interval_hours),
            force_refresh=force,
        )
    )
    console.print(f"models.dev catalog cached from {cache_entry.source_url}")


@x_app.command("scrape")
def x_scrape(
    handle: str = typer.Option(..., "--handle", help="X handle to scrape."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, help="Maximum number of posts."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    channel: str = typer.Option("", "--channel", help="Optional Chromium channel, such as chrome or msedge."),
) -> None:
    """Scrape recent X posts for one handle."""

    settings = load_config(config)
    channel = _validate_browser_channel(channel)
    if channel:
        settings.playwright.channel = channel
    context = RuntimeContext(config=settings)
    collector = XUserCollector("x.cli", [handle], limit=limit)
    try:
        items = asyncio.run(collector.collect(context))
    except XAuthenticationRequired as exc:
        console.print(f"{exc} Then retry this command.")
        raise typer.Exit(code=2) from exc
    except XScrapeError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps([_raw_item_to_jsonable(item) for item in items]))


@x_app.command("login")
def x_login(
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    channel: str = typer.Option("chrome", "--channel", help="Chromium channel to use for the visible login browser."),
    wait_seconds: int = typer.Option(600, "--wait-seconds", min=30, help="How long to keep the login browser open."),
) -> None:
    """Open a headed X browser session and persist the login profile."""

    settings = load_config(config)
    channel = _validate_browser_channel(channel)
    settings.playwright.channel = channel
    console.print(
        "Opening a headed browser. Use it like a normal browser, then close the window; "
        "the session profile will stay in the configured profile directory."
    )
    status = asyncio.run(login_to_x(RuntimeContext(config=settings), channel=channel, wait_seconds=wait_seconds))
    console.print_json(json.dumps(status))


@x_app.command("status")
def x_status(config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c")) -> None:
    """Show persistent X profile status."""

    settings = load_config(config)
    console.print_json(json.dumps(x_profile_status(settings.playwright.x.user_data_dir)))


@x_app.command("diagnose")
def x_diagnose(
    handle: str = typer.Option(..., "--handle", help="X handle to inspect."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    channel: str = typer.Option("", "--channel", help="Optional Chromium channel, such as chrome or msedge."),
    wait_seconds: int = typer.Option(5, "--wait-seconds", min=1, help="Seconds to wait after page load."),
) -> None:
    """Print public X page diagnostics for selector troubleshooting."""

    settings = load_config(config)
    channel = _validate_browser_channel(channel)
    if channel:
        settings.playwright.channel = channel
    diagnostics = asyncio.run(
        diagnose_x_profile(
            RuntimeContext(config=settings),
            handle=handle,
            channel=channel,
            wait_seconds=wait_seconds,
        )
    )
    console.print_json(json.dumps(diagnostics))


def main() -> None:
    """CLI script entrypoint."""

    app()
