"""TOML configuration loader."""

from __future__ import annotations

from pathlib import Path
import tomllib

from stock_sum.config.models import AppConfig


def load_config(path: str | Path) -> AppConfig:
    """Load and validate an application TOML configuration file."""

    config_path = Path(path)
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)
    return AppConfig.model_validate(data)


def redacted_config(config: AppConfig) -> dict:
    """Return config data safe for HTTP output."""

    return config.model_dump()
