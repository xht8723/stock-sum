"""Readable TOML config editing helpers."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import tomlkit


def write_default_config(path: str | Path, *, overwrite: bool = False) -> Path:
    """Copy the packaged example TOML to a target path."""

    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"Config already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    template = files("stock_sum.config").joinpath("example.toml").read_text()
    target.write_text(template, encoding="utf-8")
    return target


def read_toml_document(path: str | Path) -> tomlkit.TOMLDocument:
    """Read a TOML file while preserving formatting."""

    return tomlkit.parse(Path(path).read_text(encoding="utf-8"))


def get_dotted_value(document: dict[str, Any], dotted_key: str) -> Any:
    """Read a value from a nested TOML document by dotted key."""

    current: Any = document
    for part in dotted_key.split("."):
        current = current[part]
    return current


def set_dotted_value(path: str | Path, dotted_key: str, value: Any) -> None:
    """Set a TOML value by dotted key."""

    document = read_toml_document(path)
    current: Any = document
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = value
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
