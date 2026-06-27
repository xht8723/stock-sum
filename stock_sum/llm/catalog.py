"""Cache-first models.dev catalog loader."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Callable

MODELS_DEV_API_URL = "https://models.dev/api.json"


@dataclass(frozen=True)
class CatalogCacheEntry:
    """Persisted models.dev catalog payload with fetch metadata."""

    fetched_at: datetime
    source_url: str
    payload: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_cache(path: Path) -> CatalogCacheEntry | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return CatalogCacheEntry(
        fetched_at=datetime.fromisoformat(data["fetched_at"]),
        source_url=data["source_url"],
        payload=data["payload"],
    )


def _write_cache(path: Path, entry: CatalogCacheEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": entry.fetched_at.isoformat(),
                "source_url": entry.source_url,
                "payload": entry.payload,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


async def _fetch_json(url: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def load_models_dev_catalog(
    cache_path: str | Path,
    *,
    source_url: str = MODELS_DEV_API_URL,
    refresh_interval: timedelta = timedelta(days=1),
    force_refresh: bool = False,
    fetcher: Callable[[str], Any] | None = None,
) -> CatalogCacheEntry:
    """Load models.dev metadata, refreshing no more than the configured interval."""

    path = Path(cache_path)
    cached = _read_cache(path)
    cache_is_fresh = cached is not None and _now() - cached.fetched_at < refresh_interval
    if cached is not None and cache_is_fresh and not force_refresh:
        return cached

    try:
        payload = await (fetcher(source_url) if fetcher else _fetch_json(source_url))
    except Exception:
        if cached is not None:
            return cached
        raise

    entry = CatalogCacheEntry(fetched_at=_now(), source_url=source_url, payload=payload)
    _write_cache(path, entry)
    return entry
