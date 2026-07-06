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


def list_x_users(path: str | Path) -> list[dict[str, Any]]:
    """List configured X user sources."""

    return [dict(item) for item in read_toml_document(path).get("sources", {}).get("x_users", [])]


def list_subreddits(path: str | Path) -> list[dict[str, Any]]:
    """List configured subreddit sources."""

    return [dict(item) for item in read_toml_document(path).get("sources", {}).get("subreddits", [])]


def get_house_ptr_source(path: str | Path) -> dict[str, Any]:
    """Return the configured House PTR source."""

    return dict(read_toml_document(path).get("sources", {}).get("house_ptr", {}))


def set_house_ptr_source(
    path: str | Path,
    *,
    enabled: bool,
    year: int | None,
    refresh_ttl_seconds: int,
    download_concurrency: int,
    parse_concurrency: int,
    zip_url_template: str,
    pdf_url_template: str,
) -> str:
    """Set House PTR source configuration."""

    document = read_toml_document(path)
    sources = _sources_table(document)
    source = tomlkit.table()
    source["enabled"] = enabled
    source["year"] = year or 0
    source["refresh_ttl_seconds"] = refresh_ttl_seconds
    source["download_concurrency"] = download_concurrency
    source["parse_concurrency"] = parse_concurrency
    source["zip_url_template"] = zip_url_template
    source["pdf_url_template"] = pdf_url_template
    sources["house_ptr"] = source

    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return "house.ptr"


def add_x_user(
    path: str | Path,
    handle: str,
    *,
    enabled: bool,
    limit: int,
    lookback_hours: int,
    overwrite: bool = False,
) -> str:
    """Add or replace an X user source."""

    normalized = _normalize_x_handle(handle)
    document = read_toml_document(path)
    sources = _sources_table(document)
    x_users = _source_array(sources, "x_users")
    existing = _find_source_index(x_users, "handle", normalized)
    if existing is not None and not overwrite:
        raise KeyError(f"X user already exists: {normalized}")

    source = tomlkit.table()
    source["handle"] = normalized
    source["enabled"] = enabled
    source["limit"] = limit
    source["lookback_hours"] = lookback_hours
    if existing is None:
        x_users.append(source)
    else:
        x_users[existing] = source

    collector_id = f"x.{normalized}"
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def delete_x_user(path: str | Path, handle: str) -> str:
    """Delete an X user source."""

    normalized = _normalize_x_handle(handle)
    document = read_toml_document(path)
    x_users = document.get("sources", {}).get("x_users", [])
    existing = _find_source_index(x_users, "handle", normalized)
    if existing is None:
        raise KeyError(f"X user does not exist: {normalized}")
    del x_users[existing]

    collector_id = f"x.{normalized}"
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def add_subreddit(
    path: str | Path,
    subreddit: str,
    *,
    enabled: bool,
    sort: str,
    timeframe: str,
    limit: int,
    lookback_hours: int,
    trim: bool,
    include_comments: bool,
    comments_per_post: int,
    overwrite: bool = False,
) -> str:
    """Add or replace a subreddit source."""

    normalized = _normalize_subreddit(subreddit)
    document = read_toml_document(path)
    sources = _sources_table(document)
    subreddits = _source_array(sources, "subreddits")
    existing = _find_source_index(subreddits, "subreddit", normalized)
    if existing is not None and not overwrite:
        raise KeyError(f"Subreddit already exists: {normalized}")

    source = tomlkit.table()
    source["subreddit"] = normalized
    source["enabled"] = enabled
    source["sort"] = sort
    source["timeframe"] = timeframe
    source["limit"] = limit
    source["lookback_hours"] = lookback_hours
    source["trim"] = trim
    source["include_comments"] = include_comments
    source["comments_per_post"] = comments_per_post
    if existing is None:
        subreddits.append(source)
    else:
        subreddits[existing] = source

    collector_id = f"reddit.{normalized}"
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def delete_subreddit(path: str | Path, subreddit: str) -> str:
    """Delete a subreddit source."""

    normalized = _normalize_subreddit(subreddit)
    document = read_toml_document(path)
    subreddits = document.get("sources", {}).get("subreddits", [])
    existing = _find_source_index(subreddits, "subreddit", normalized)
    if existing is None:
        raise KeyError(f"Subreddit does not exist: {normalized}")
    del subreddits[existing]

    collector_id = f"reddit.{normalized}"
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def _sources_table(document: tomlkit.TOMLDocument) -> Any:
    return document.setdefault("sources", tomlkit.table())


def _source_array(sources: Any, name: str) -> Any:
    if name not in sources:
        sources[name] = tomlkit.aot()
    return sources[name]


def _find_source_index(items: list[Any], key: str, value: str) -> int | None:
    for index, item in enumerate(items):
        if str(item.get(key, "")).lower() == value.lower():
            return index
    return None


def _normalize_x_handle(handle: str) -> str:
    normalized = handle.strip().lstrip("@")
    if not normalized:
        raise ValueError("X handle cannot be empty.")
    return normalized


def _normalize_subreddit(subreddit: str) -> str:
    normalized = subreddit.strip().strip("/").removeprefix("r/")
    if not normalized:
        raise ValueError("Subreddit cannot be empty.")
    return normalized
