"""Command line interface for stock-sum."""

from __future__ import annotations

from datetime import timedelta
from dataclasses import asdict
from pathlib import Path
from typing import Any
import asyncio
import ast
import json
import os

import typer
import uvicorn
from rich.console import Console

from stock_sum.config.loader import load_config
from stock_sum.collectors.playwright.capitol_trades import CAPITOL_TRADES_URL, scrape_capitol_trades
from stock_sum.config.writer import (
    add_profile,
    add_subreddit,
    add_x_user,
    delete_profile,
    delete_subreddit,
    delete_x_user,
    edit_profile,
    get_dotted_value,
    get_profile,
    list_subreddits,
    list_profiles,
    list_x_users,
    read_toml_document,
    set_dotted_value,
    write_default_config,
)
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import StockSumError
from stock_sum.core.models import CollectionRunResult, PipelineCollectionResult
from stock_sum.core.pipeline import ReportPipeline
from stock_sum.llm.catalog import load_models_dev_catalog
from stock_sum.llm.registry import build_llm_client
from stock_sum.media.downloader import MediaDownloader
from stock_sum.reports.presentation import PresentationRenderError, PresentationRenderer
from stock_sum.reports.summary_input import SummaryInputBuilder
from stock_sum.service.daemon import build_daemon
from stock_sum.storage.sqlite import SQLiteStorageRepository

app = typer.Typer(help="Trading information summarization service.")
config_app = typer.Typer(help="Manage TOML configuration.")
profile_app = typer.Typer(help="Manage report profiles in TOML configuration.")
x_user_app = typer.Typer(help="Manage X user sources in TOML configuration.")
subreddit_app = typer.Typer(help="Manage subreddit sources in TOML configuration.")
payload_app = typer.Typer(help="Build LLM-ready payloads from collected data.")
llm_app = typer.Typer(help="Run LLM summarization against payloads.")
report_app = typer.Typer(help="Render final presentation reports.")
app.add_typer(config_app, name="config")
app.add_typer(payload_app, name="payload")
app.add_typer(llm_app, name="llm")
app.add_typer(report_app, name="report")
config_app.add_typer(profile_app, name="profile")
config_app.add_typer(x_user_app, name="x-user")
config_app.add_typer(subreddit_app, name="subreddit")
console = Console()


def _parse_value(raw: str) -> Any:
    try:
        return ast.literal_eval(raw)
    except Exception:
        return raw


def _collection_run_to_jsonable(result: CollectionRunResult) -> dict[str, Any]:
    return asdict(result)


def _pipeline_result_to_jsonable(result: PipelineCollectionResult) -> dict[str, Any]:
    data = asdict(result)
    data["collected_count"] = result.collected_count
    data["inserted_count"] = result.inserted_count
    data["updated_count"] = result.updated_count
    return data


def _split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_env_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE pairs from a local env file without overriding the process."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@app.command("run-report")
def run_report(
    profile: str = typer.Option(..., "--profile", "-p", help="Report profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Manually request a report pipeline run."""

    _load_env_file()
    settings = load_config(config)
    pipeline = ReportPipeline(RuntimeContext(config=settings))
    try:
        result = asyncio.run(pipeline.run_report(profile))
    except StockSumError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps(_pipeline_result_to_jsonable(result)))


@app.command()
def collect(
    collector: str | None = typer.Option(None, "--collector", help="Configured collector id to run."),
    profile: str | None = typer.Option(None, "--profile", help="Configured report profile whose collectors should run."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Collect configured source data and persist it to SQLite."""

    _load_env_file()
    if bool(collector) == bool(profile):
        raise typer.BadParameter("Pass exactly one of --collector or --profile.")

    settings = load_config(config)
    pipeline = ReportPipeline(RuntimeContext(config=settings))
    try:
        if collector:
            result = asyncio.run(pipeline.collect_collector(collector))
            console.print_json(json.dumps(_collection_run_to_jsonable(result)))
            return

        result = asyncio.run(pipeline.run_report(profile or ""))
        console.print_json(json.dumps(_pipeline_result_to_jsonable(result)))
    except StockSumError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc


@app.command()
def daemon(
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Run the HTTP service and scheduler host."""

    _load_env_file()
    settings = load_config(config)
    uvicorn.run(build_daemon(settings), host=host or settings.server.host, port=port or settings.server.port)


@payload_app.command("build")
def payload_build(
    profile: str = typer.Option(..., "--profile", "-p", help="Report profile name."),
    output: Path = typer.Option(..., "--output", "-o", help="JSON output path."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    download_images: bool = typer.Option(
        False,
        "--download-images/--no-download-images",
        help="Download eligible image media before writing the payload.",
    ),
    mode: str = typer.Option("full", "--mode", help="Payload mode: full, compact, or vision."),
    max_images_per_post: int = typer.Option(3, "--max-images-per-post", min=0, help="Maximum image media per post."),
    max_images_total: int = typer.Option(20, "--max-images-total", min=0, help="Maximum image media in the payload."),
) -> None:
    """Build an LLM-ready summary input payload from stored collection data."""

    settings = load_config(config)
    repository = SQLiteStorageRepository(settings.storage.sqlite_path)
    downloader = MediaDownloader(settings.media, repository) if download_images else None
    builder = SummaryInputBuilder(config=settings, repository=repository, downloader=downloader)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = asyncio.run(builder.build(profile=profile, download_images=download_images))
        payload_data = payload.to_dict(
            mode=mode,
            max_images_per_post=max_images_per_post,
            max_images_total=max_images_total,
        )
    except (StockSumError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    output.write_text(json.dumps(payload_data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"Wrote {output}")


@llm_app.command("summarize")
def llm_summarize(
    profile: str = typer.Option("default", "--profile", "-p", help="Report profile name."),
    payload: Path | None = typer.Option(None, "--payload", help="Existing compact/vision payload JSON file."),
    output: Path = typer.Option(..., "--output", "-o", help="Summary response JSON output path."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    instructions: str | None = typer.Option(None, "--instructions", help="Additional summarization instructions."),
    max_images_per_post: int = typer.Option(3, "--max-images-per-post", min=0, help="Maximum image refs per post when building payload."),
    max_images_total: int = typer.Option(20, "--max-images-total", min=0, help="Maximum image refs when building payload."),
) -> None:
    """Summarize an LLM-ready payload with the configured LLM provider."""

    _load_env_file()
    settings = load_config(config)
    try:
        if payload is not None:
            payload_data = json.loads(payload.read_text(encoding="utf-8"))
        else:
            repository = SQLiteStorageRepository(settings.storage.sqlite_path)
            builder = SummaryInputBuilder(config=settings, repository=repository)
            summary_input = asyncio.run(builder.build(profile=profile, download_images=False))
            payload_data = summary_input.to_dict(
                mode="compact",
                max_images_per_post=max_images_per_post,
                max_images_total=max_images_total,
            )
        client = build_llm_client(settings.llm)
        summary = asyncio.run(client.summarize(payload_data, instructions=instructions))
    except (OSError, ValueError, StockSumError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    response_data = {
        "profile": profile,
        "provider": settings.llm.provider,
        "model": summary.model,
        "summary_text": summary.text,
        "summary": summary.metadata.get("parsed"),
        "input_media": payload_data.get("media", {}) if isinstance(payload_data, dict) else {},
        "metadata": {key: value for key, value in summary.metadata.items() if key != "parsed"},
    }
    output.write_text(json.dumps(response_data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"Wrote {output}")


@report_app.command("render")
def report_render(
    input_path: Path = typer.Option(..., "--input", "-i", help="LLM summarize response JSON file."),
    output: Path = typer.Option(..., "--output", "-o", help="Rendered report output file."),
    mode: str = typer.Option("html", "--mode", help="Presentation mode: html, markdown, or text."),
    title: str = typer.Option("Market Social Digest", "--title", help="Report title."),
    include_capitol_trades: bool = typer.Option(
        False,
        "--include-capitol-trades/--no-capitol-trades",
        help="Scrape Capitol Trades after loading the LLM response and include it in the final report.",
    ),
    capitol_trades_url: str = typer.Option(CAPITOL_TRADES_URL, "--capitol-trades-url", help="Capitol Trades URL to scrape."),
    capitol_trades_limit: int = typer.Option(12, "--capitol-trades-limit", min=1, help="Maximum visible Capitol Trades rows."),
) -> None:
    """Render an LLM response into a final presentation artifact."""

    try:
        response = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(response, dict):
            raise PresentationRenderError("Input response JSON must be an object.")
        if include_capitol_trades:
            snapshot = asyncio.run(
                scrape_capitol_trades(url=capitol_trades_url, limit=capitol_trades_limit)
            )
            response["capitol_trades"] = snapshot.to_dict()
        rendered = PresentationRenderer(title=title).render(response, mode=mode)
    except (OSError, ValueError, PresentationRenderError, StockSumError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    except OSError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Wrote {output}")


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


@profile_app.command("list")
def profile_list(config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c")) -> None:
    """List report profile names."""

    console.print_json(json.dumps({"profiles": list_profiles(config)}))


@profile_app.command("show")
def profile_show(
    name: str = typer.Argument(..., help="Profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Show one report profile."""

    try:
        profile = get_profile(config, name)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print_json(json.dumps({"name": name, "profile": profile}))


@profile_app.command("add")
def profile_add(
    name: str = typer.Argument(..., help="Profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    timezone: str = typer.Option("UTC", "--timezone", help="Profile timezone."),
    schedule: str = typer.Option("0 8 * * 1-5", "--schedule", help="Cron schedule."),
    collectors: str = typer.Option("", "--collectors", help="Comma-separated collector ids."),
    deliveries: str = typer.Option("", "--deliveries", help="Comma-separated delivery ids."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing profile."),
) -> None:
    """Add a report profile."""

    try:
        add_profile(
            config,
            name,
            timezone=timezone,
            schedule=schedule,
            collector_ids=_split_csv(collectors) or [],
            delivery_ids=_split_csv(deliveries) or [],
            overwrite=overwrite,
        )
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Added profile {name}.")


@profile_app.command("edit")
def profile_edit(
    name: str = typer.Argument(..., help="Profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    timezone: str | None = typer.Option(None, "--timezone", help="Profile timezone."),
    schedule: str | None = typer.Option(None, "--schedule", help="Cron schedule."),
    collectors: str | None = typer.Option(None, "--collectors", help="Comma-separated collector ids."),
    deliveries: str | None = typer.Option(None, "--deliveries", help="Comma-separated delivery ids."),
) -> None:
    """Edit a report profile."""

    try:
        edit_profile(
            config,
            name,
            timezone=timezone,
            schedule=schedule,
            collector_ids=_split_csv(collectors),
            delivery_ids=_split_csv(deliveries),
        )
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Updated profile {name}.")


@profile_app.command("delete")
def profile_delete(
    name: str = typer.Argument(..., help="Profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Delete a report profile."""

    try:
        delete_profile(config, name)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Deleted profile {name}.")


@x_user_app.command("list")
def x_user_list(config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c")) -> None:
    """List X user sources."""

    console.print_json(json.dumps({"x_users": list_x_users(config)}))


@x_user_app.command("add")
def x_user_add(
    handle: str = typer.Argument(..., help="X handle, with or without @."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum posts to collect."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether this source can be collected."),
    profile: str | None = typer.Option(None, "--profile", help="Also add x.<handle> to this report profile."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing source."),
) -> None:
    """Add an X user source."""

    try:
        collector_id = add_x_user(
            config,
            handle,
            enabled=enabled,
            limit=limit,
            profile=profile,
            overwrite=overwrite,
        )
    except (KeyError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Added X user source {collector_id}.")


@x_user_app.command("delete")
def x_user_delete(
    handle: str = typer.Argument(..., help="X handle, with or without @."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    profile: str | None = typer.Option(None, "--profile", help="Also remove x.<handle> from this report profile."),
) -> None:
    """Delete an X user source."""

    try:
        collector_id = delete_x_user(config, handle, profile=profile)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Deleted X user source {collector_id}.")


@subreddit_app.command("list")
def subreddit_list(config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c")) -> None:
    """List subreddit sources."""

    console.print_json(json.dumps({"subreddits": list_subreddits(config)}))


@subreddit_app.command("add")
def subreddit_add(
    subreddit: str = typer.Argument(..., help="Subreddit name, with or without r/."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    sort: str = typer.Option("new", "--sort", help="Reddit sort mode."),
    timeframe: str = typer.Option("day", "--timeframe", help="Timeframe used when sort=top."),
    limit: int = typer.Option(10, "--limit", min=1, help="Maximum posts to collect."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether this source can be collected."),
    trim: bool = typer.Option(True, "--trim/--no-trim", help="Request trimmed provider responses."),
    include_comments: bool = typer.Option(False, "--include-comments/--no-comments", help="Collect comments too."),
    comments_per_post: int = typer.Option(0, "--comments-per-post", min=0, help="Maximum comments per post."),
    profile: str | None = typer.Option(None, "--profile", help="Also add reddit.<subreddit> to this report profile."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing source."),
) -> None:
    """Add a subreddit source."""

    try:
        collector_id = add_subreddit(
            config,
            subreddit,
            enabled=enabled,
            sort=sort,
            timeframe=timeframe,
            limit=limit,
            trim=trim,
            include_comments=include_comments,
            comments_per_post=comments_per_post,
            profile=profile,
            overwrite=overwrite,
        )
    except (KeyError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Added subreddit source {collector_id}.")


@subreddit_app.command("delete")
def subreddit_delete(
    subreddit: str = typer.Argument(..., help="Subreddit name, with or without r/."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Also remove reddit.<subreddit> from this report profile.",
    ),
) -> None:
    """Delete a subreddit source."""

    try:
        collector_id = delete_subreddit(config, subreddit, profile=profile)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Deleted subreddit source {collector_id}.")


def main() -> None:
    """CLI script entrypoint."""

    app()
