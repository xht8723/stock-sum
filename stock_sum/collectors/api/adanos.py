"""Adanos market sentiment API client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
import asyncio
import hashlib
import json
import os

import httpx

from stock_sum.config.models import AdanosProviderConfig

AdanosPlatform = Literal["reddit", "x"]
AdanosCategory = Literal["stocks", "sectors"]

ADANOS_FETCH_LIMIT = 100

ADANOS_ENDPOINTS: tuple[tuple[AdanosPlatform, AdanosCategory, str], ...] = (
    ("reddit", "stocks", "/reddit/stocks/v1/trending"),
    ("reddit", "sectors", "/reddit/stocks/v1/trending/sectors"),
    ("x", "stocks", "/x/stocks/v1/trending"),
    ("x", "sectors", "/x/stocks/v1/trending/sectors"),
)


@dataclass(frozen=True)
class AdanosEndpointRequest:
    """One deterministic Adanos endpoint request."""

    platform: AdanosPlatform
    category: AdanosCategory
    endpoint: str
    request_args: dict[str, Any]


def build_adanos_trending_requests(*, from_date: date, to_date: date) -> list[AdanosEndpointRequest]:
    """Build the fixed Adanos trendings request set in source order."""

    request_args = {"from": from_date.isoformat(), "to": to_date.isoformat(), "limit": ADANOS_FETCH_LIMIT}
    return [
        AdanosEndpointRequest(
            platform=platform,
            category=category,
            endpoint=endpoint,
            request_args=dict(request_args),
        )
        for platform, category, endpoint in ADANOS_ENDPOINTS
    ]


def adanos_response_cache_key(config: AdanosProviderConfig, request: AdanosEndpointRequest) -> str:
    """Return a versioned cache key without persisting the API secret."""

    payload = {
        "version": 1,
        "provider": "adanos",
        "base_url": _normalize_base_url(config.base_url),
        "api_key_env": config.api_key_env.strip(),
        "platform": request.platform,
        "category": request.category,
        "endpoint": request.endpoint,
        "request_args": request.request_args,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


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
        requests: list[AdanosEndpointRequest] | None = None,
    ) -> AdanosTrendingsResult:
        """Fetch Reddit/X trending stocks and sectors from Adanos."""

        endpoint_requests = list(requests) if requests is not None else build_adanos_trending_requests(
            from_date=from_date,
            to_date=to_date,
        )
        if not endpoint_requests:
            return AdanosTrendingsResult(skipped=False)

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            return AdanosTrendingsResult(skipped=True)

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
                    request=request,
                )
                for request in endpoint_requests
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
        request: AdanosEndpointRequest,
    ) -> AdanosEndpointResult:
        async with semaphore:
            fetched_at = datetime.now(timezone.utc)
            try:
                response = await client.get(request.endpoint, params=request.request_args)
                raw_text = response.text
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("Adanos response was not a list.")
                rows = [item for item in payload if isinstance(item, dict)]
                return AdanosEndpointResult(
                    platform=request.platform,
                    category=request.category,
                    endpoint=request.endpoint,
                    request_args=dict(request.request_args),
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
                    platform=request.platform,
                    category=request.category,
                    endpoint=request.endpoint,
                    request_args=dict(request.request_args),
                    status="failed",
                    raw_response_text=raw_text,
                    error=str(exc),
                    fetched_at=fetched_at,
                )


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))
