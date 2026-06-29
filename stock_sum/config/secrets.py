"""Env-file helpers for local secret management."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
import os


def read_env_file(path: str | Path) -> dict[str, str]:
    """Read simple KEY=VALUE env files."""

    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def write_env_file(path: str | Path, values: Mapping[str, str]) -> Path:
    """Write a simple KEY=VALUE env file with restrictive permissions where supported."""

    env_path = Path(path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={_quote_env_value(value)}" for key, value in sorted(values.items())]
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _chmod_private(env_path)
    return env_path


def set_secret(path: str | Path, name: str, value: str) -> Path:
    """Set one env-file secret."""

    values = read_env_file(path)
    values[_normalize_name(name)] = value
    return write_env_file(path, values)


def remove_secret(path: str | Path, name: str) -> bool:
    """Remove one env-file secret if present."""

    values = read_env_file(path)
    normalized = _normalize_name(name)
    removed = normalized in values
    values.pop(normalized, None)
    write_env_file(path, values)
    return removed


def load_env_file(path: str | Path, *, override: bool = False) -> None:
    """Load env-file values into the current process."""

    for key, value in read_env_file(path).items():
        if override or key not in os.environ:
            os.environ[key] = value


def required_secret_names(*, xpoz_api_key_env: str, llm_api_key_env: str) -> list[str]:
    """Return required secret env-var names for the current implemented pipeline."""

    names = [xpoz_api_key_env, llm_api_key_env]
    result: list[str] = []
    for name in names:
        if name and name not in result:
            result.append(name)
    return result


def missing_secret_names(names: list[str], *, env_file: str | Path | None = None) -> list[str]:
    """Return required names missing from process env and optional env file."""

    file_values = read_env_file(env_file) if env_file is not None else {}
    return [name for name in names if not os.getenv(name) and not file_values.get(name)]


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Secret name cannot be empty.")
    if "=" in normalized:
        raise ValueError("Secret name cannot contain '='.")
    return normalized


def _quote_env_value(value: str) -> str:
    if not value:
        return '""'
    if any(char.isspace() for char in value) or any(char in value for char in ['"', "'", "#"]):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
