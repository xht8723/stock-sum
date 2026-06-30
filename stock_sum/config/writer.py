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


def list_profiles(path: str | Path) -> list[str]:
    """List configured report profile names."""

    document = read_toml_document(path)
    return sorted(document.get("reports", {}).keys())


def get_profile(path: str | Path, name: str) -> dict[str, Any]:
    """Return one configured report profile."""

    document = read_toml_document(path)
    try:
        profile = document["reports"][name]
    except KeyError as exc:
        raise KeyError(f"Profile does not exist: {name}") from exc
    return dict(profile)


def add_profile(
    path: str | Path,
    name: str,
    *,
    timezone: str,
    schedule: str,
    collector_ids: list[str],
    delivery_ids: list[str],
    overwrite: bool = False,
) -> None:
    """Add a report profile to a TOML config."""

    document = read_toml_document(path)
    reports = document.setdefault("reports", tomlkit.table())
    if name in reports and not overwrite:
        raise KeyError(f"Profile already exists: {name}")

    profile = tomlkit.table()
    profile["timezone"] = timezone
    profile["schedule"] = schedule
    profile["collector_ids"] = collector_ids
    profile["delivery_ids"] = delivery_ids
    reports[name] = profile
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")


def edit_profile(
    path: str | Path,
    name: str,
    *,
    timezone: str | None = None,
    schedule: str | None = None,
    collector_ids: list[str] | None = None,
    delivery_ids: list[str] | None = None,
) -> None:
    """Edit fields on an existing report profile."""

    document = read_toml_document(path)
    try:
        profile = document["reports"][name]
    except KeyError as exc:
        raise KeyError(f"Profile does not exist: {name}") from exc

    if timezone is not None:
        profile["timezone"] = timezone
    if schedule is not None:
        profile["schedule"] = schedule
    if collector_ids is not None:
        profile["collector_ids"] = collector_ids
    if delivery_ids is not None:
        profile["delivery_ids"] = delivery_ids
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")


def delete_profile(path: str | Path, name: str) -> None:
    """Delete a report profile from a TOML config."""

    document = read_toml_document(path)
    try:
        del document["reports"][name]
    except KeyError as exc:
        raise KeyError(f"Profile does not exist: {name}") from exc
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")


def list_x_users(path: str | Path) -> list[dict[str, Any]]:
    """List configured X user sources."""

    return [dict(item) for item in read_toml_document(path).get("sources", {}).get("x_users", [])]


def list_subreddits(path: str | Path) -> list[dict[str, Any]]:
    """List configured subreddit sources."""

    return [dict(item) for item in read_toml_document(path).get("sources", {}).get("subreddits", [])]


def add_x_user(
    path: str | Path,
    handle: str,
    *,
    enabled: bool,
    limit: int,
    lookback_hours: int,
    profile: str | None = None,
    overwrite: bool = False,
) -> str:
    """Add or replace an X user source and optionally attach it to a profile."""

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
    if profile:
        _add_collector_to_profile(document, profile, collector_id)
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def delete_x_user(path: str | Path, handle: str, *, profile: str | None = None) -> str:
    """Delete an X user source and optionally detach it from a profile."""

    normalized = _normalize_x_handle(handle)
    document = read_toml_document(path)
    x_users = document.get("sources", {}).get("x_users", [])
    existing = _find_source_index(x_users, "handle", normalized)
    if existing is None:
        raise KeyError(f"X user does not exist: {normalized}")
    del x_users[existing]

    collector_id = f"x.{normalized}"
    if profile:
        _remove_collector_from_profile(document, profile, collector_id)
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
    profile: str | None = None,
    overwrite: bool = False,
) -> str:
    """Add or replace a subreddit source and optionally attach it to a profile."""

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
    if profile:
        _add_collector_to_profile(document, profile, collector_id)
    Path(path).write_text(tomlkit.dumps(document), encoding="utf-8")
    return collector_id


def delete_subreddit(path: str | Path, subreddit: str, *, profile: str | None = None) -> str:
    """Delete a subreddit source and optionally detach it from a profile."""

    normalized = _normalize_subreddit(subreddit)
    document = read_toml_document(path)
    subreddits = document.get("sources", {}).get("subreddits", [])
    existing = _find_source_index(subreddits, "subreddit", normalized)
    if existing is None:
        raise KeyError(f"Subreddit does not exist: {normalized}")
    del subreddits[existing]

    collector_id = f"reddit.{normalized}"
    if profile:
        _remove_collector_from_profile(document, profile, collector_id)
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


def _add_collector_to_profile(document: tomlkit.TOMLDocument, profile_name: str, collector_id: str) -> None:
    try:
        profile = document["reports"][profile_name]
    except KeyError as exc:
        raise KeyError(f"Profile does not exist: {profile_name}") from exc
    collector_ids = list(profile.get("collector_ids", []))
    if collector_id not in collector_ids:
        collector_ids.append(collector_id)
    profile["collector_ids"] = collector_ids


def _remove_collector_from_profile(document: tomlkit.TOMLDocument, profile_name: str, collector_id: str) -> None:
    try:
        profile = document["reports"][profile_name]
    except KeyError as exc:
        raise KeyError(f"Profile does not exist: {profile_name}") from exc
    profile["collector_ids"] = [item for item in profile.get("collector_ids", []) if item != collector_id]


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
