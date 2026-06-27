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
from stock_sum.service.daemon import build_daemon

app = typer.Typer(help="Trading information summarization service.")
config_app = typer.Typer(help="Manage TOML configuration.")
profile_app = typer.Typer(help="Manage report profiles in TOML configuration.")
x_user_app = typer.Typer(help="Manage X user sources in TOML configuration.")
subreddit_app = typer.Typer(help="Manage subreddit sources in TOML configuration.")
app.add_typer(config_app, name="config")
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


@app.command("run-report")
def run_report(
    profile: str = typer.Option(..., "--profile", "-p", help="Report profile name."),
    config: Path = typer.Option(Path("stock_sum/config/example.toml"), "--config", "-c"),
) -> None:
    """Manually request a report pipeline run."""

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
    trim: bool = typer.Option(True, "--trim/--no-trim", help="Request trimmed provider responses."),
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
            trim=trim,
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
