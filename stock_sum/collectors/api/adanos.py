"""Adanos market sentiment API client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal
import asyncio
import json
import os

import httpx

from stock_sum.config.models import AdanosProviderConfig

AdanosPlatform = Literal["reddit", "x"]
AdanosCategory = Literal["stocks", "sectors"]

ADANOS_FETCH_LIMIT = 100

_ENDPOINTS: tuple[tuple[AdanosPlatform, AdanosCategory, str], ...] = (
    ("reddit", "stocks", "/reddit/stocks/v1/trending"),
    ("reddit", "sectors", "/reddit/stocks/v1/trending/sectors"),
    ("x", "stocks", "/x/stocks/v1/trending"),
    ("x", "sectors", "/x/stocks/v1/trending/sectors"),
)


@dataclass(frozen=True)
class AdanosEndpointResult:
    """One Adanos endpoint response or recoverable failure."""

    platform: AdanosPlatform
    category: AdanosCategory
    endpoint: str
    request_args: dict[str, Any]
    status: Literal["succeeded", "failed"]
    raw_response_text: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class AdanosTrendingsResult:
    """Combined Adanos trendings fetch result."""

    skipped: bool
    responses: list[AdanosEndpointResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class AdanosClient:
    """Small async client for Adanos trendings endpoints."""

    def __init__(
        self,
        config: AdanosProviderConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.http_client = http_client
        self.transport = transport

    async def fetch_trendings(
        self,
        *,
        from_date: date,
        to_date: date,
    ) -> AdanosTrendingsResult:
        """Fetch Reddit/X trending stocks and sectors from Adanos."""

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            return AdanosTrendingsResult(skipped=True)

        request_args = {"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": ADANOS_FETCH_LIMIT}
        semaphore = asyncio.Semaphore(self.config.max_concurrent_requests)
        owns_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=self.config.timeout_seconds,
            headers={"X-API-Key": api_key},
            transport=self.transport,
        )
        try:
            tasks = [
                self._fetch_endpoint(
                    client,
                    semaphore,
                    platform=platform,
                    category=category,
                    endpoint=endpoint,
                    request_args=request_args,
                )
                for platform, category, endpoint in _ENDPOINTS
            ]
            responses = await asyncio.gather(*tasks)
        finally:
            if owns_client:
                await client.aclose()

        warnings = [
            f"Adanos {response.platform} {response.category} failed: {response.error}"
            for response in responses
            if response.status == "failed"
        ]
        return AdanosTrendingsResult(skipped=False, responses=responses, warnings=warnings)

    async def _fetch_endpoint(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        *,
        platform: AdanosPlatform,
        category: AdanosCategory,
        endpoint: str,
        request_args: dict[str, Any],
    ) -> AdanosEndpointResult:
        async with semaphore:
            fetched_at = datetime.now(timezone.utc)
            try:
                response = await client.get(endpoint, params=request_args)
                raw_text = response.text
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Adanos response was not a list.")
                rows = [item for item in payload if isinstance(item, dict)]
                return AdanosEndpointResult(
                    platform=platform,
                    category=category,
                    endpoint=endpoint,
                    request_args=dict(request_args),
                    status="succeeded",
                    raw_response_text=raw_text,
                    rows=rows,
                    fetched_at=fetched_at,
                )
            except Exception as exc:
                raw_text = ""
                if "response" in locals():
                    raw_text = getattr(response, "text", "") or ""
                if not raw_text:
                    raw_text = json.dumps({"error": str(exc)}, ensure_ascii=False)
                return AdanosEndpointResult(
                    platform=platform,
                    category=category,
                    endpoint=endpoint,
                    request_args=dict(request_args),
                    status="failed",
                    raw_response_text=raw_text,
                    error=str(exc),
                    fetched_at=fetched_at,
                )
