"""Command line interface for stock-sum."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
import asyncio
import ast
import json

import typer
import uvicorn
from rich.console import Console

from stock_sum.config.loader import load_config
from stock_sum.config.secrets import (
    load_env_file,
    read_env_file,
    remove_secret,
    set_secret,
    write_env_file,
)
from stock_sum.config.writer import (
    add_subreddit,
    add_x_user,
    delete_subreddit,
    delete_x_user,
    get_house_ptr_source,
    get_dotted_value,
    list_subreddits,
    list_x_users,
    read_toml_document,
    set_house_ptr_source,
    set_dotted_value,
    write_default_config,
)
from stock_sum.cli_serialization import _collection_run_to_jsonable, _pipeline_result_to_jsonable
from stock_sum.cli_setup import (
    _managed_targets_from_config,
    _remove_reset_target,
    _resolve_config_path,
    _setup_issues,
    _sqlite_reset_targets,
    _unique_paths,
    _validate_runtime_setup,
)
from stock_sum.cli_state import (
    DEFAULT_ENV_FILE,
    DEFAULT_EXAMPLE_CONFIG_PATH,
    DEFAULT_LOCAL_CONFIG_PATH,
    DEFAULT_SETUP_STATE_FILE,
    _read_setup_state,
    _resolve_config_option,
    _resolve_data_dir_option,
    _resolve_env_file_option,
    _resolve_path,
    _state_file_path,
    _write_setup_state,
)
from stock_sum.core.context import RuntimeContext
from stock_sum.core.errors import ConfigurationError, StockSumError
from stock_sum.core.pipeline import ReportPipeline
from stock_sum.llm.analysis import LLMAnalysisService
from stock_sum.llm.catalog import load_models_dev_catalog
from stock_sum.llm.registry import build_llm_client, get_llm_provider, list_llm_providers
from stock_sum.media.downloader import MediaDownloader
from stock_sum.retention import DataRetentionService, RetentionSummary
from stock_sum.reports.presentation import PresentationRenderError, PresentationRenderer
from stock_sum.reports.summary_input import SummaryInputBuilder
from stock_sum.service.daemon import build_daemon
from stock_sum.storage.sqlite import SQLiteStorageRepository
from stock_sum.worker_client import run_cli_worker

app = typer.Typer(help="Trading information summarization service.")
config_app = typer.Typer(help="Manage TOML configuration.")
setup_app = typer.Typer(help="First-run setup and environment validation.")
secrets_app = typer.Typer(help="Manage local env-file secrets.")
x_user_app = typer.Typer(help="Manage X user sources in TOML configuration.")
subreddit_app = typer.Typer(help="Manage subreddit sources in TOML configuration.")
house_ptr_app = typer.Typer(help="Manage House PTR disclosure source in TOML configuration.")
payload_app = typer.Typer(help="Build LLM-ready payloads from collected data.")
llm_app = typer.Typer(help="Run LLM summarization against payloads.")
report_app = typer.Typer(help="Render final presentation reports.")
retention_app = typer.Typer(help="Inspect and prune managed runtime data.")
database_app = typer.Typer(help="Inspect and reset SQLite storage.")
app.add_typer(config_app, name="config")
app.add_typer(setup_app, name="setup")
app.add_typer(secrets_app, name="secrets")
app.add_typer(payload_app, name="payload")
app.add_typer(llm_app, name="llm")
app.add_typer(report_app, name="report")
app.add_typer(retention_app, name="retention")
app.add_typer(database_app, name="database")
config_app.add_typer(x_user_app, name="x-user")
config_app.add_typer(subreddit_app, name="subreddit")
config_app.add_typer(house_ptr_app, name="house-ptr")
console = Console()


def _parse_config_get_args(args: list[str]) -> tuple[Path, str]:
    if len(args) == 1:
        return _resolve_config_option(None, fallback=DEFAULT_LOCAL_CONFIG_PATH), args[0]
    if len(args) == 2:
        return Path(args[0]), args[1]
    raise typer.BadParameter("Use `config get KEY` or `config get PATH KEY`.")


def _parse_config_set_args(args: list[str]) -> tuple[Path, str, str]:
    if len(args) == 2:
        config = _resolve_config_option(None, fallback=DEFAULT_LOCAL_CONFIG_PATH)
        return config, args[0], args[1]
    if len(args) == 3:
        return Path(args[0]), args[1], args[2]
    raise typer.BadParameter("Use `config set KEY VALUE` or `config set PATH KEY VALUE`.")


def _parse_value(raw: str) -> Any:
    try:
        return ast.literal_eval(raw)
    except Exception:
        return raw


def _load_env_file(path: Path = Path(".env"), *, override: bool = False) -> None:
    """Load simple KEY=VALUE pairs from a local env file."""

    load_env_file(path, override=override)


def _run_retention_after_pipeline(settings) -> RetentionSummary | None:
    if not settings.retention.prune_after_pipeline:
        return None
    try:
        summary = asyncio.run(DataRetentionService(settings).prune())
    except Exception as exc:
        console.print_json(json.dumps({"retention": {"errors": [str(exc)]}}))
        return None
    if summary.bytes_deleted > 0 or summary.errors:
        console.print_json(json.dumps({"retention": summary.to_dict()}))
    return summary


@app.command()
def collect(
    collector: str | None = typer.Option(None, "--collector", help="Configured collector id to run."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
) -> None:
    """Collect configured source data and persist it to SQLite."""

    config = _resolve_config_option(config)
    env_file = _resolve_env_file_option(env_file)
    _load_env_file(env_file)

    settings = load_config(config)
    code = run_cli_worker(settings, "cli_collect", {"collector": collector})
    if code != 0:
        raise typer.Exit(code=code)


@app.command()
def daemon(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path for runtime secret updates. Defaults to remembered setup path, then .env."),
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
) -> None:
    """Run the HTTP service."""

    config = _resolve_config_option(config)
    env_file = _resolve_env_file_option(env_file)
    _load_env_file(env_file, override=True)
    settings = load_config(config)
    try:
        _validate_runtime_setup(settings, env_file=env_file)
    except ConfigurationError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    uvicorn.run(
        build_daemon(settings, config_path=str(config), env_file=str(env_file)),
        host=host or settings.server.host,
        port=port or settings.server.port,
    )


@setup_app.command("init")
def setup_init(
    config: Path = typer.Option(Path("config.toml"), "--config", "-c", help="Config TOML path to write."),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="Env file path for secrets."),
    state_file: Path = typer.Option(DEFAULT_SETUP_STATE_FILE, "--state-file", help="Non-secret setup state path."),
    overwrite: bool = typer.Option(True, "--overwrite/--no-overwrite", help="Replace an existing config file."),
    yes: bool = typer.Option(False, "--yes", help="Accept defaults and use provided key options."),
    host: str | None = typer.Option(None, "--host", help="HTTP server host."),
    port: int | None = typer.Option(None, "--port", help="HTTP server port."),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM provider id."),
    xpoz_api_key: str | None = typer.Option(None, "--xpoz-api-key", help="Xpoz API key to store in env file."),
    adanos_api_key: str | None = typer.Option(None, "--adanos-api-key", help="Optional Adanos API key to store in env file."),
    llm_api_key: str | None = typer.Option(None, "--llm-api-key", help="LLM API key to store in env file."),
) -> None:
    """Run the first-time interactive setup wizard."""

    providers = list_llm_providers()
    default_provider = "deepseek"
    console.print("stock-sum setup")
    console.print("Supported LLM providers:")
    for index, provider in enumerate(providers, start=1):
        marker = "implemented" if provider.implemented else "planned"
        console.print(f"{index}. {provider.provider_id} - {provider.display_name} ({marker})")

    if not yes:
        config = Path(typer.prompt("Config path", default=str(config)))
        env_file = Path(typer.prompt("Env file path", default=str(env_file)))
        host = typer.prompt("HTTP host", default=host or "127.0.0.1")
        port = int(typer.prompt("HTTP port", default=str(port or 8000)))
        llm_provider = typer.prompt("LLM provider", default=llm_provider or default_provider)
    else:
        host = host or "127.0.0.1"
        port = port or 8000
        llm_provider = llm_provider or default_provider

    descriptor = get_llm_provider(llm_provider or default_provider)
    if not descriptor.implemented:
        console.print(f"LLM provider is not implemented yet: {descriptor.provider_id}")
        raise typer.Exit(code=1)

    if xpoz_api_key is None and not yes:
        xpoz_api_key = typer.prompt("XPOZ_API_KEY")
    if adanos_api_key is None and not yes:
        adanos_api_key = typer.prompt("ADANOS_API_KEY (optional, blank to skip)", default="")
    if llm_api_key is None and not yes:
        llm_api_key = typer.prompt(descriptor.api_key_env)
    if xpoz_api_key is None or llm_api_key is None:
        console.print("Missing API key input. Pass --xpoz-api-key and --llm-api-key when using --yes.")
        raise typer.Exit(code=1)

    try:
        write_default_config(config, overwrite=overwrite)
        set_dotted_value(config, "server.host", host)
        set_dotted_value(config, "server.port", port)
        set_dotted_value(config, "llm.provider", descriptor.provider_id)
        set_dotted_value(config, "llm.model", descriptor.default_model)
        set_dotted_value(config, "llm.api_key_env", descriptor.api_key_env)
        set_dotted_value(config, "llm.base_url", descriptor.base_url)
    except (FileExistsError, OSError, KeyError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    env_values = read_env_file(env_file)
    env_values["XPOZ_API_KEY"] = xpoz_api_key
    if adanos_api_key:
        env_values["ADANOS_API_KEY"] = adanos_api_key
    env_values[descriptor.api_key_env] = llm_api_key
    write_env_file(env_file, env_values)
    _write_setup_state(state_file, config=config, env_file=env_file, data_dir=config.parent / "data")

    console.print(f"Wrote config: {config}")
    console.print(f"Wrote env file: {env_file}")
    console.print(f"Wrote setup state: {state_file}")
    console.print("Next steps:")
    console.print(f"1. Validate setup: stock-sum setup check --config {config} --env-file {env_file}")
    console.print(f"2. Start service: stock-sum daemon --config {config}")
    console.print("3. Request social report: POST /v1/social-reports/jobs or use Redbot /recent_posts.")
    console.print("4. Request trading report: POST /v1/trading-reports/jobs or use Redbot /ptr_search.")


@setup_app.command("check")
def setup_check(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then config.toml."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
) -> None:
    """Validate config, required secrets, and local runtime paths."""

    config = _resolve_config_option(config, fallback=DEFAULT_LOCAL_CONFIG_PATH)
    env_file = _resolve_env_file_option(env_file)
    issues = _setup_issues(config, env_file)
    if issues:
        for issue in issues:
            console.print(f"[red]ERROR[/red] {issue}")
        raise typer.Exit(code=1)
    console.print("Setup check passed.")


@setup_app.command("reset")
def setup_reset(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config TOML path to remove."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path to remove."),
    data_dir: Path | None = typer.Option(None, "--data-dir", help="Local data directory to remove."),
    state_file: Path = typer.Option(DEFAULT_SETUP_STATE_FILE, "--state-file", help="Non-secret setup state path."),
    yes: bool = typer.Option(False, "--yes", help="Skip interactive confirmations."),
) -> None:
    """Reset local stock-sum state to a clean first-run install."""

    config_path = _resolve_config_option(config, fallback=DEFAULT_LOCAL_CONFIG_PATH, state_file=state_file)
    env_path = _resolve_env_file_option(env_file, state_file=state_file)
    data_path = _resolve_data_dir_option(data_dir, state_file=state_file)
    targets = _unique_paths(
        [
            config_path,
            env_path,
            data_path,
            *_managed_targets_from_config(_resolve_path(config_path)),
            state_file,
        ]
    )
    console.print("[red]WARNING[/red] This will delete local stock-sum setup state.")
    console.print("Targets:")
    for target in targets:
        marker = "exists" if target.exists() else "not present"
        console.print(f"- {target} ({marker})")

    if not yes:
        if not typer.confirm("Continue with reset?"):
            console.print("Reset cancelled.")
            raise typer.Exit(code=1)

    removed: list[str] = []
    for target in targets:
        try:
            if _remove_reset_target(target):
                removed.append(str(target))
        except OSError as exc:
            console.print(f"Failed to remove {target}: {exc}")
            raise typer.Exit(code=1) from exc

    console.print_json(json.dumps({"removed": removed, "status": "reset"}))
    console.print("Run `stock-sum setup init` to configure a fresh install.")


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Environment variable name."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
    value: str | None = typer.Option(None, "--value", help="Secret value. Omit to prompt securely."),
) -> None:
    """Set one env-file secret without printing its value."""

    env_file = _resolve_env_file_option(env_file)
    try:
        secret_value = value if value is not None else typer.prompt(f"Value for {name}")
        set_secret(env_file, name, secret_value)
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"Set {name} in {env_file}")


@secrets_app.command("list")
def secrets_list(env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env.")) -> None:
    """List env-file secret names without values."""

    env_file = _resolve_env_file_option(env_file)
    names = sorted(read_env_file(env_file).keys())
    console.print_json(json.dumps({"env_file": str(env_file), "secrets": names}))


@secrets_app.command("remove")
def secrets_remove(
    name: str = typer.Argument(..., help="Environment variable name."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
) -> None:
    """Remove one env-file secret."""

    env_file = _resolve_env_file_option(env_file)
    try:
        removed = remove_secret(env_file, name)
    except (OSError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    console.print(f"{'Removed' if removed else 'Not present'} {name} in {env_file}")


@payload_app.command("build")
def payload_build(
    output: Path = typer.Option(..., "--output", "-o", help="JSON output path."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
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

    config = _resolve_config_option(config)
    settings = load_config(config)
    repository = SQLiteStorageRepository(settings.storage.sqlite_path)
    downloader = MediaDownloader(settings.media, repository) if download_images else None
    builder = SummaryInputBuilder(config=settings, repository=repository, downloader=downloader)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = asyncio.run(builder.build(download_images=download_images))
        payload_data = payload.to_dict(
            mode=mode,
            max_images_per_post=max_images_per_post,
            max_images_total=max_images_total,
        )
        if download_images:
            _run_retention_after_pipeline(settings)
    except (StockSumError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc

    output.write_text(json.dumps(payload_data, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"Wrote {output}")


@retention_app.command("status")
def retention_status(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
) -> None:
    """Show managed runtime data usage without deleting anything."""

    config = _resolve_config_option(config)
    settings = load_config(config)
    summary = asyncio.run(DataRetentionService(settings).status())
    console.print_json(json.dumps(summary.to_dict()))


@retention_app.command("prune")
def retention_prune(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Preview deletions unless --apply is used."),
) -> None:
    """Prune managed runtime data according to retention limits."""

    config = _resolve_config_option(config)
    settings = load_config(config)
    summary = asyncio.run(DataRetentionService(settings).prune(dry_run=dry_run))
    console.print_json(json.dumps(summary.to_dict()))


@database_app.command("reset")
def database_reset(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    sqlite_path: Path | None = typer.Option(None, "--sqlite-path", help="Override the SQLite file path from config."),
    yes: bool = typer.Option(False, "--yes", help="Skip interactive confirmations."),
) -> None:
    """Delete the SQLite database and sidecar files so schema is recreated cleanly."""

    if sqlite_path is None:
        config = _resolve_config_option(config)
        settings = load_config(config)
        sqlite_path = Path(settings.storage.sqlite_path)
    if str(sqlite_path) == ":memory:":
        console.print("Cannot reset in-memory SQLite storage.")
        raise typer.Exit(code=1)

    targets = _sqlite_reset_targets(sqlite_path)
    console.print("[red]WARNING[/red] This will delete collected SQLite history and LLM analysis rows.")
    console.print("Stop the stock-sum daemon before resetting the database.")
    console.print("Targets:")
    for target in targets:
        marker = "exists" if target.exists() else "not present"
        console.print(f"- {target} ({marker})")

    if not yes:
        if not typer.confirm("Continue with database reset?"):
            console.print("Database reset cancelled.")
            raise typer.Exit(code=1)
        confirmation = typer.prompt("Type RESET DATABASE to confirm deletion")
        if confirmation != "RESET DATABASE":
            console.print("Database reset cancelled.")
            raise typer.Exit(code=1)

    removed: list[str] = []
    for target in targets:
        try:
            if target.exists():
                target.unlink()
                removed.append(str(target))
        except OSError as exc:
            console.print(f"Failed to remove {target}: {exc}")
            raise typer.Exit(code=1) from exc

    console.print_json(json.dumps({"removed": removed, "sqlite_path": str(sqlite_path), "status": "reset"}))
    console.print("Restart the daemon or run the next collection/report job to recreate the schema.")


@llm_app.command("summarize")
def llm_summarize(
    payload: Path | None = typer.Option(None, "--payload", help="Existing compact/vision payload JSON file."),
    output: Path = typer.Option(..., "--output", "-o", help="Summary response JSON output path."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
    instructions: str | None = typer.Option(None, "--instructions", help="Additional summarization instructions."),
    max_images_per_post: int = typer.Option(3, "--max-images-per-post", min=0, help="Maximum image refs per post when building payload."),
    max_images_total: int = typer.Option(20, "--max-images-total", min=0, help="Maximum image refs when building payload."),
) -> None:
    """Summarize an LLM-ready payload with the configured LLM provider."""

    config = _resolve_config_option(config)
    env_file = _resolve_env_file_option(env_file)
    _load_env_file(env_file)
    settings = load_config(config)
    code = run_cli_worker(
        settings,
        "cli_llm_summarize",
        {
            "payload_path": str(payload) if payload is not None else None,
            "output_path": str(output),
            "instructions": instructions,
            "max_images_per_post": max_images_per_post,
            "max_images_total": max_images_total,
        },
    )
    if code != 0:
        raise typer.Exit(code=code)


@llm_app.command("analyze")
def llm_analyze(
    output: Path = typer.Option(..., "--output", "-o", help="Analysis summary JSON output path."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    env_file: Path | None = typer.Option(None, "--env-file", help="Env file path. Defaults to remembered setup path, then .env."),
    instructions: str | None = typer.Option(None, "--instructions", help="Additional analysis instructions."),
    max_images_per_post: int = typer.Option(3, "--max-images-per-post", min=0, help="Maximum image refs per post when building payload."),
    max_images_total: int = typer.Option(20, "--max-images-total", min=0, help="Maximum image refs when building payload."),
) -> None:
    """Run chunked LLM analysis from stored collection data and persist analysis rows."""

    config = _resolve_config_option(config)
    env_file = _resolve_env_file_option(env_file)
    _load_env_file(env_file)
    settings = load_config(config)
    code = run_cli_worker(
        settings,
        "cli_llm_analyze",
        {
            "output_path": str(output),
            "instructions": instructions,
            "max_images_per_post": max_images_per_post,
            "max_images_total": max_images_total,
        },
    )
    if code != 0:
        raise typer.Exit(code=code)


@llm_app.command("providers")
def llm_providers() -> None:
    """List LLM providers known to stock-sum."""

    data = [
        {
            "provider": provider.provider_id,
            "display_name": provider.display_name,
            "default_model": provider.default_model,
            "api_key_env": provider.api_key_env,
            "implemented": provider.implemented,
            "base_url": provider.base_url,
            "setup_notes": provider.setup_notes,
        }
        for provider in list_llm_providers()
    ]
    console.print_json(json.dumps({"providers": data}))


@report_app.command("render")
def report_render(
    input_path: Path = typer.Option(..., "--input", "-i", help="LLM summarize response JSON file."),
    output: Path = typer.Option(..., "--output", "-o", help="Rendered report output file."),
    mode: str = typer.Option("html", "--mode", help="Presentation mode: html, markdown, discord, or text."),
    detail: str = typer.Option("minimum", "--detail", help="Social report detail: minimum, medium, or full."),
    title: str = typer.Option("Market Social Digest", "--title", help="Report title."),
) -> None:
    """Render an LLM response into a final presentation artifact."""

    try:
        response = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(response, dict):
            raise PresentationRenderError("Input response JSON must be an object.")
        rendered = PresentationRenderer(title=title).render(response, mode=mode, detail=detail)
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
def config_validate(path: Path | None = typer.Argument(None, help="Config path. Defaults to remembered setup path, then config.toml.")) -> None:
    """Validate a TOML config file."""

    path = _resolve_config_option(path, fallback=DEFAULT_LOCAL_CONFIG_PATH)
    load_config(path)
    console.print("Config is valid.")


@config_app.command("get")
def config_get(args: list[str] = typer.Argument(..., help="KEY, or PATH KEY for explicit config.")) -> None:
    """Get a dotted config value."""

    path, key = _parse_config_get_args(args)
    document = read_toml_document(path)
    console.print(get_dotted_value(document, key))


@config_app.command("set")
def config_set(args: list[str] = typer.Argument(..., help="KEY VALUE, or PATH KEY VALUE for explicit config.")) -> None:
    """Set a dotted config value."""

    path, key, value = _parse_config_set_args(args)
    set_dotted_value(path, key, _parse_value(value))
    console.print(f"Updated {key}")


@config_app.command("sync")
def config_sync(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    force: bool = typer.Option(False, "--force", help="Force a models.dev refresh."),
) -> None:
    """Refresh cache-backed external configuration metadata."""

    config = _resolve_config_option(config)
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


@x_user_app.command("list")
def x_user_list(config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config.")) -> None:
    """List X user sources."""

    config = _resolve_config_option(config)
    console.print_json(json.dumps({"x_users": list_x_users(config)}))


@x_user_app.command("add")
def x_user_add(
    handle: str = typer.Argument(..., help="X handle, with or without @."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    limit: int = typer.Option(100, "--limit", min=1, help="Provider fetch cap before 24-hour filtering."),
    lookback_hours: int = typer.Option(24, "--lookback-hours", min=1, help="Only keep posts from this many recent hours."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether this source can be collected."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing source."),
) -> None:
    """Add an X user source."""

    config = _resolve_config_option(config)
    try:
        collector_id = add_x_user(
            config,
            handle,
            enabled=enabled,
            limit=limit,
            lookback_hours=lookback_hours,
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
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
) -> None:
    """Delete an X user source."""

    config = _resolve_config_option(config)
    try:
        collector_id = delete_x_user(config, handle)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Deleted X user source {collector_id}.")


@subreddit_app.command("list")
def subreddit_list(config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config.")) -> None:
    """List subreddit sources."""

    config = _resolve_config_option(config)
    console.print_json(json.dumps({"subreddits": list_subreddits(config)}))


@subreddit_app.command("add")
def subreddit_add(
    subreddit: str = typer.Argument(..., help="Subreddit name, with or without r/."),
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    sort: str = typer.Option("new", "--sort", help="Reddit sort mode."),
    timeframe: str = typer.Option("day", "--timeframe", help="Timeframe used when sort=top."),
    limit: int = typer.Option(100, "--limit", min=1, help="Provider fetch cap before lookback filtering."),
    lookback_hours: int = typer.Option(24, "--lookback-hours", min=1, help="Only keep posts from this many recent hours."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether this source can be collected."),
    trim: bool = typer.Option(True, "--trim/--no-trim", help="Request trimmed provider responses."),
    include_comments: bool = typer.Option(True, "--include-comments/--no-comments", help="Collect comments too."),
    comments_per_post: int = typer.Option(10, "--comments-per-post", min=0, help="Maximum comments per post."),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace an existing source."),
) -> None:
    """Add a subreddit source."""

    config = _resolve_config_option(config)
    try:
        collector_id = add_subreddit(
            config,
            subreddit,
            enabled=enabled,
            sort=sort,
            timeframe=timeframe,
            limit=limit,
            lookback_hours=lookback_hours,
            trim=trim,
            include_comments=include_comments,
            comments_per_post=comments_per_post,
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
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
) -> None:
    """Delete a subreddit source."""

    config = _resolve_config_option(config)
    try:
        collector_id = delete_subreddit(config, subreddit)
    except KeyError as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Deleted subreddit source {collector_id}.")


@house_ptr_app.command("show")
def house_ptr_show(config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config.")) -> None:
    """Show House PTR source settings."""

    config = _resolve_config_option(config)
    console.print_json(json.dumps({"house_ptr": get_house_ptr_source(config)}))


@house_ptr_app.command("set")
def house_ptr_set(
    config: Path | None = typer.Option(None, "--config", "-c", help="Config path. Defaults to remembered setup path, then example config."),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Whether House PTR can be collected."),
    year: int = typer.Option(0, "--year", min=0, help="Disclosure year, or 0 for current UTC year."),
    refresh_ttl_seconds: int = typer.Option(21600, "--refresh-ttl-seconds", min=0, help="Seconds before House PTR data is considered stale."),
    download_concurrency: int = typer.Option(1, "--download-concurrency", min=1, help="Concurrent PDF downloads."),
    parse_concurrency: int = typer.Option(1, "--parse-concurrency", min=1, help="Concurrent PDF table parse jobs."),
    zip_url_template: str = typer.Option(
        "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip",
        "--zip-url-template",
    ),
    pdf_url_template: str = typer.Option(
        "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf",
        "--pdf-url-template",
    ),
) -> None:
    """Set House PTR source settings."""

    config = _resolve_config_option(config)
    try:
        collector_id = set_house_ptr_source(
            config,
            enabled=enabled,
            year=year or None,
            refresh_ttl_seconds=refresh_ttl_seconds,
            download_concurrency=download_concurrency,
            parse_concurrency=parse_concurrency,
            zip_url_template=zip_url_template,
            pdf_url_template=pdf_url_template,
        )
    except (KeyError, ValueError) as exc:
        console.print(str(exc))
        raise typer.Exit(code=1) from exc
    load_config(config)
    console.print(f"Updated House PTR source {collector_id}.")


def main() -> None:
    """CLI script entrypoint."""

    app()
