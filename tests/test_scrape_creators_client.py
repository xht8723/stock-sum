"""Scrape Creators client tests."""

import httpx
import pytest

from stock_sum.collectors.api.scrape_creators import (
    ScrapeCreatorsAuthError,
    ScrapeCreatorsClient,
    ScrapeCreatorsCreditsError,
    ScrapeCreatorsRetryableError,
)


async def test_client_sends_api_key_header(monkeypatch) -> None:
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["x-api-key"] = request.headers["x-api-key"]
        return httpx.Response(200, json={"tweets": []})

    monkeypatch.setenv("SCRAPE_CREATORS_API_KEY", "secret")
    client = ScrapeCreatorsClient(
        api_key_env="SCRAPE_CREATORS_API_KEY",
        base_url="https://api.scrapecreators.com",
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    await client.twitter_user_tweets(handle="aleabitoreddit", trim=True)

    assert seen_headers["x-api-key"] == "secret"


async def test_client_missing_api_key_fails(monkeypatch) -> None:
    monkeypatch.delenv("SCRAPE_CREATORS_API_KEY", raising=False)
    client = ScrapeCreatorsClient(
        api_key_env="SCRAPE_CREATORS_API_KEY",
        base_url="https://api.scrapecreators.com",
        timeout_seconds=30,
    )

    with pytest.raises(ScrapeCreatorsAuthError):
        await client.twitter_user_tweets(handle="aleabitoreddit", trim=True)


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, ScrapeCreatorsAuthError),
        (402, ScrapeCreatorsCreditsError),
        (500, ScrapeCreatorsRetryableError),
    ],
)
async def test_client_maps_http_errors(monkeypatch, status_code, expected_error) -> None:
    monkeypatch.setenv("SCRAPE_CREATORS_API_KEY", "secret")
    client = ScrapeCreatorsClient(
        api_key_env="SCRAPE_CREATORS_API_KEY",
        base_url="https://api.scrapecreators.com",
        timeout_seconds=30,
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json={"error": "failed"})),
    )

    with pytest.raises(expected_error):
        await client.twitter_user_tweets(handle="aleabitoreddit", trim=True)
