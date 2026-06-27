"""SQLite database connection scaffolding."""

from __future__ import annotations

from pathlib import Path


def sqlite_url(sqlite_path: str | Path) -> str:
    """Return a SQLAlchemy-compatible SQLite URL."""

    return f"sqlite+aiosqlite:///{Path(sqlite_path).as_posix()}"
