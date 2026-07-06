"""Adanos trendings client tests."""

from __future__ import annotations

from datetime import date

import httpx

from stock_sum.collectors.api.adanos import ADANOS_FETCH_LIMIT, AdanosClient
from stock_sum.config.models import AdanosProviderConfig


async def test_adanos_client_sends_api_key_and_fetch_params(monkeypatch) -> None:
    seen: list[tuple[str, str | None, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.path,
                request.headers.get("x-api-key"),
                dict(request.url.params),
            )
        )
        return httpx.Response(
            200,
            json=[
                {
                    "ticker": "NVDA",
                    "company_name": "NVIDIA Corp",
                    "trend": "up",
                    "mentions": 12,
                    "bullish_pct": 60,
                    "bearish_pct": 20,
                }
            ],
        )

    monkeypatch.setenv("ADANOS_API_KEY", "adanos-secret")
    result = await AdanosClient(
        AdanosProviderConfig(api_key_env="ADANOS_API_KEY"),
        transport=httpx.MockTransport(handler),
    ).fetch_trendings(from_date=date(2026, 7, 1), to_date=date(2026, 7, 6))

    assert result.skipped is False
    assert result.warnings == []
    assert {path for path, _key, _params in seen} == {
        "/reddit/stocks/v1/trending",
        "/reddit/stocks/v1/trending/sectors",
        "/x/stocks/v1/trending",
        "/x/stocks/v1/trending/sectors",
    }
    assert all(key == "adanos-secret" for _path, key, _params in seen)
    assert all(params == {"from": "2026-07-01", "to": "2026-07-06", "limit": str(ADANOS_FETCH_LIMIT)} for _path, _key, params in seen)


async def test_adanos_client_missing_key_skips_without_warning(monkeypatch) -> None:
    monkeypatch.delenv("ADANOS_API_KEY", raising=False)

    result = await AdanosClient(AdanosProviderConfig(api_key_env="ADANOS_API_KEY")).fetch_trendings(
        from_date=date(2026, 7, 1),
        to_date=date(2026, 7, 6),
    )

    assert result.skipped is True
    assert result.responses == []
    assert result.warnings == []


async def test_adanos_client_partial_failure_returns_warning(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sectors"):
            return httpx.Response(500, text="temporary failure")
        return httpx.Response(200, json=[{"ticker": "AMD", "mentions": 5}])

    monkeypatch.setenv("ADANOS_API_KEY", "adanos-secret")
    async with httpx.AsyncClient(
        base_url="https://api.adanos.org",
        headers={"X-API-Key": "adanos-secret"},
        transport=httpx.MockTransport(handler),
    ) as http_client:
        result = await AdanosClient(
            AdanosProviderConfig(api_key_env="ADANOS_API_KEY"),
            http_client=http_client,
        ).fetch_trendings(from_date=date(2026, 7, 1), to_date=date(2026, 7, 6))

    assert len(result.responses) == 4
    assert len([response for response in result.responses if response.status == "succeeded"]) == 2
    assert len(result.warnings) == 2
