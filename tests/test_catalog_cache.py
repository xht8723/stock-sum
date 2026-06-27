"""models.dev catalog cache behavior tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
import json

from stock_sum.llm.catalog import load_models_dev_catalog


def test_catalog_loader_prefers_fresh_cache(tmp_path) -> None:
    cache_path = tmp_path / "models.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "source_url": "https://models.dev/api.json",
                "payload": {"cached": True},
            }
        ),
        encoding="utf-8",
    )

    async def run():
        async def fetcher(url: str):
            raise AssertionError("fresh cache should not fetch")

        return await load_models_dev_catalog(cache_path, fetcher=fetcher)

    entry = asyncio.run(run())
    assert entry.payload == {"cached": True}


def test_catalog_loader_refreshes_stale_cache(tmp_path) -> None:
    cache_path = tmp_path / "models.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                "source_url": "https://models.dev/api.json",
                "payload": {"cached": True},
            }
        ),
        encoding="utf-8",
    )

    async def run():
        async def fetcher(url: str):
            return {"fresh": True}

        return await load_models_dev_catalog(cache_path, fetcher=fetcher)

    entry = asyncio.run(run())
    assert entry.payload == {"fresh": True}


def test_catalog_loader_falls_back_to_stale_cache(tmp_path) -> None:
    cache_path = tmp_path / "models.json"
    cache_path.write_text(
        json.dumps(
            {
                "fetched_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                "source_url": "https://models.dev/api.json",
                "payload": {"cached": True},
            }
        ),
        encoding="utf-8",
    )

    async def run():
        async def fetcher(url: str):
            raise RuntimeError("network unavailable")

        return await load_models_dev_catalog(cache_path, fetcher=fetcher)

    entry = asyncio.run(run())
    assert entry.payload == {"cached": True}
