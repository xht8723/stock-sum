"""Runtime config/env management for the local HTTP API."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, TypeVar
import os

from stock_sum.config.loader import load_config
from stock_sum.config.models import AppConfig
from stock_sum.config.secrets import (
    load_env_file,
    missing_secret_names,
    read_env_file,
    remove_secret,
    required_secret_names,
    set_secret,
)


T = TypeVar("T")


class RuntimeConfigError(Exception):
    """Raised when runtime config management is unavailable or invalid."""


class RuntimeConfigManager:
    """Owns the active daemon config and optional TOML/env backing files."""

    def __init__(
        self,
        config: AppConfig,
        *,
        config_path: str | Path | None = None,
        env_file: str | Path | None = None,
    ) -> None:
        self._config = config
        self.config_path = Path(config_path) if config_path is not None else None
        self.env_file = Path(env_file) if env_file is not None else None
        self.version = 0

    @classmethod
    def from_paths(cls, config_path: str | Path, env_file: str | Path | None = None) -> "RuntimeConfigManager":
        """Load config/env files and build a runtime manager."""

        if env_file is not None:
            load_env_file(env_file, override=True)
        config = load_config(Path(config_path))
        return cls(config, config_path=config_path, env_file=env_file)

    @property
    def config(self) -> AppConfig:
        return self._config

    def reload(self) -> AppConfig:
        """Reload active config and env-file values."""

        if self.env_file is not None:
            load_env_file(self.env_file, override=True)
        if self.config_path is not None:
            self._config = load_config(self.config_path)
        self.version += 1
        return self._config

    def mutate_config(self, callback: Callable[[Path], T]) -> T:
        """Run one TOML mutation against the backing config path, then reload."""

        path = self.require_config_path()
        result = callback(path)
        self.reload()
        return result

    def set_secret_value(self, name: str, value: str) -> None:
        """Write one secret to the env file and process env."""

        path = self.require_env_file()
        set_secret(path, name, value)
        os.environ[name] = value

    def remove_secret_value(self, name: str) -> bool:
        """Remove one secret from the env file and process env."""

        path = self.require_env_file()
        removed = remove_secret(path, name)
        os.environ.pop(name, None)
        return removed

    def secret_names(self) -> list[str]:
        """List env-file secret names without values."""

        if self.env_file is None:
            return []
        return sorted(read_env_file(self.env_file).keys())

    def setup_issues(self) -> list[str]:
        """Return actionable setup issues for the active runtime config."""

        issues: list[str] = []
        if self.config_path is None:
            issues.append("Runtime config path is not configured.")
        elif not self.config_path.exists():
            issues.append(f"Config file does not exist: {self.config_path}")
        if self.env_file is None:
            issues.append("Runtime env file path is not configured.")

        secret_names = required_secret_names(
            xpoz_api_key_env=self.config.providers.xpoz.api_key_env,
            llm_api_key_env=self.config.llm.api_key_env,
        )
        missing = missing_secret_names(secret_names, env_file=self.env_file)
        if missing:
            issues.append(f"Missing required secrets: {', '.join(missing)}")

        sqlite_parent = Path(self.config.storage.sqlite_path).parent
        if sqlite_parent and not sqlite_parent.exists():
            try:
                sqlite_parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                issues.append(f"SQLite directory is not writable: {sqlite_parent} ({exc})")
        return issues

    def require_config_path(self) -> Path:
        if self.config_path is None:
            raise RuntimeConfigError("Config file path is not available for this daemon.")
        return self.config_path

    def require_env_file(self) -> Path:
        if self.env_file is None:
            raise RuntimeConfigError("Env file path is not available for this daemon.")
        return self.env_file
