"""Remembered path helpers for the stock-sum CLI."""

from __future__ import annotations

from pathlib import Path
import json


DEFAULT_SETUP_STATE_FILE = Path(".stock-sum-state.json")
DEFAULT_ENV_FILE = Path(".env")
DEFAULT_LOCAL_CONFIG_PATH = Path("config.toml")
DEFAULT_EXAMPLE_CONFIG_PATH = Path("stock_sum/config/example.toml")


def state_file_path(state_file: Path | None = None) -> Path:
    return state_file or DEFAULT_SETUP_STATE_FILE


def remembered_path(key: str, *, state_file: Path | None = None) -> Path | None:
    state = read_setup_state(state_file_path(state_file))
    value = state.get(key)
    return Path(value) if value else None


def resolve_remembered_path(
    explicit: Path | None,
    *,
    state_key: str,
    fallback: Path,
    state_file: Path | None = None,
) -> Path:
    if explicit is not None:
        return explicit
    return remembered_path(state_key, state_file=state_file) or fallback


def resolve_config_option(
    config: Path | None,
    *,
    fallback: Path = DEFAULT_EXAMPLE_CONFIG_PATH,
    state_file: Path | None = None,
) -> Path:
    return resolve_remembered_path(config, state_key="config", fallback=fallback, state_file=state_file)


def resolve_env_file_option(env_file: Path | None, *, state_file: Path | None = None) -> Path:
    return resolve_remembered_path(env_file, state_key="env_file", fallback=DEFAULT_ENV_FILE, state_file=state_file)


def resolve_data_dir_option(data_dir: Path | None, *, state_file: Path | None = None) -> Path:
    return resolve_remembered_path(data_dir, state_key="data_dir", fallback=Path("data"), state_file=state_file)


def resolve_path(path: Path) -> Path:
    """Resolve a path without requiring it to already exist."""

    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def read_setup_state(state_file: Path) -> dict[str, str]:
    """Read remembered non-secret setup paths."""

    try:
        return json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_setup_state(state_file: Path, *, config: Path, env_file: Path, data_dir: Path) -> None:
    """Remember setup paths so reset targets the active install."""

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {
                "config": str(resolve_path(config)),
                "env_file": str(resolve_path(env_file)),
                "data_dir": str(resolve_path(data_dir)),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


_state_file_path = state_file_path
_remembered_path = remembered_path
_resolve_remembered_path = resolve_remembered_path
_resolve_config_option = resolve_config_option
_resolve_env_file_option = resolve_env_file_option
_resolve_data_dir_option = resolve_data_dir_option
_resolve_path = resolve_path
_read_setup_state = read_setup_state
_write_setup_state = write_setup_state
