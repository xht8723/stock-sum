"""Setup, validation, and reset helpers for the stock-sum CLI."""

from __future__ import annotations

from pathlib import Path
import shutil

from stock_sum.cli_state import resolve_path
from stock_sum.config.loader import load_config
from stock_sum.config.secrets import missing_secret_names, required_secret_names
from stock_sum.core.errors import ConfigurationError
from stock_sum.llm.registry import get_llm_provider


def setup_issues(config_path: Path, env_file: Path) -> list[str]:
    """Return actionable setup issues."""

    issues: list[str] = []
    try:
        settings = load_config(config_path)
    except Exception as exc:
        return [f"Config is invalid: {exc}"]

    try:
        provider = get_llm_provider(settings.llm.provider)
        if not provider.implemented:
            issues.append(f"LLM provider is not implemented: {settings.llm.provider}")
    except ConfigurationError as exc:
        issues.append(str(exc))

    required = required_secret_names(
        xpoz_api_key_env=settings.providers.xpoz.api_key_env,
        llm_api_key_env=settings.llm.api_key_env,
    )
    missing = missing_secret_names(required, env_file=env_file)
    if missing:
        issues.append(f"Missing required secrets: {', '.join(missing)}")

    storage_parent = Path(settings.storage.sqlite_path).parent
    try:
        storage_parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        issues.append(f"SQLite directory is not writable: {storage_parent} ({exc})")
    return issues


def validate_runtime_setup(settings, *, env_file: Path = Path(".env")) -> None:
    """Fail daemon startup with actionable setup guidance."""

    missing = missing_secret_names(
        required_secret_names(
            xpoz_api_key_env=settings.providers.xpoz.api_key_env,
            llm_api_key_env=settings.llm.api_key_env,
        ),
        env_file=env_file,
    )
    if missing:
        raise ConfigurationError(
            "Missing required environment variables: "
            f"{', '.join(missing)}. Run `stock-sum setup init` or set them in an env file."
        )


def remove_reset_target(path: Path) -> bool:
    """Remove one setup reset target if it exists."""

    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def resolve_config_path(value: str, config_path: Path) -> Path:
    """Resolve a TOML path value relative to the config file directory."""

    path = Path(value)
    if path.is_absolute():
        return resolve_path(path)
    return resolve_path(config_path.parent / path)


def managed_targets_from_config(config_path: Path) -> list[Path]:
    """Return runtime output paths from the active config."""

    if not config_path.exists():
        return []
    try:
        settings = load_config(config_path)
    except Exception:
        return []
    sqlite_path = resolve_config_path(settings.storage.sqlite_path, config_path)
    return [
        resolve_config_path(settings.server.artifact_dir, config_path),
        resolve_config_path(settings.media.root_dir, config_path),
        *sqlite_reset_targets(sqlite_path),
    ]


def unique_paths(paths: list[Path]) -> list[Path]:
    """Deduplicate paths while preserving order."""

    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        resolved = resolve_path(path)
        key = str(resolved).casefold()
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def sqlite_reset_targets(sqlite_path: Path) -> list[Path]:
    """Return SQLite database and sidecar files that can be reset together."""

    return [
        sqlite_path,
        Path(f"{sqlite_path}-wal"),
        Path(f"{sqlite_path}-shm"),
        Path(f"{sqlite_path}-journal"),
    ]


_setup_issues = setup_issues
_validate_runtime_setup = validate_runtime_setup
_remove_reset_target = remove_reset_target
_resolve_config_path = resolve_config_path
_managed_targets_from_config = managed_targets_from_config
_unique_paths = unique_paths
_sqlite_reset_targets = sqlite_reset_targets
