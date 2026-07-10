"""Management API tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from stock_sum.api.app import create_app
from stock_sum.api.runtime_config import RuntimeConfigManager
from stock_sum.config.loader import load_config
from stock_sum.config.writer import write_default_config


def test_source_management_hot_reloads_config(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)

    add_x = client.post(
        "/v1/sources/x-users",
        json={"handle": "newuser", "limit": 50, "lookback_hours": 12, "enabled": True},
    )
    assert add_x.status_code == 200
    assert add_x.json()["collector_id"] == "x.newuser"

    config = load_config(config_path)
    assert any(
        source.handle == "newuser" and source.enabled and source.limit == 50 and source.lookback_hours == 12
        for source in config.sources.x_users
    )

    add_reddit = client.post("/v1/sources/subreddits", json={"subreddit": "stocks"})
    assert add_reddit.status_code == 200
    config = load_config(config_path)
    reddit_source = next(source for source in config.sources.subreddits if source.subreddit == "stocks")
    assert reddit_source.include_comments is True
    assert reddit_source.comments_per_post == 10


def test_reddit_source_delete_updates_global_sources(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)
    add_reddit = client.post("/v1/sources/subreddits", json={"subreddit": "wallstreetbets"})
    assert add_reddit.status_code == 200

    response = client.delete("/v1/sources/subreddits/wallstreetbets")

    assert response.status_code == 200
    config = load_config(config_path)
    assert all(source.subreddit != "wallstreetbets" for source in config.sources.subreddits)


def test_removed_management_endpoints_are_not_registered(tmp_path) -> None:
    client, _config_path, _env_file = _management_client(tmp_path)

    assert client.post("/v1/collect/jobs").status_code == 404
    assert client.get("/v1/sources/house-ptr").status_code == 404
    assert client.patch("/v1/sources/house-ptr", json={}).status_code == 404
    assert client.get("/v1/llm/providers").status_code == 404
    assert client.patch("/v1/llm/config", json={"provider": "deepseek"}).status_code == 404
    assert client.get("/v1/secrets").status_code == 404
    assert client.put("/v1/secrets/TEST_SECRET", json={"value": "secret"}).status_code == 404
    assert client.get("/v1/setup/check").status_code == 404
    assert client.get("/v1/retention/status").status_code == 404


def _management_client(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    write_default_config(config_path, overwrite=True)
    env_file.write_text("XPOZ_API_KEY=x\nDEEPSEEK_API_KEY=d\n", encoding="utf-8")
    runtime = RuntimeConfigManager.from_paths(config_path, env_file)
    client = TestClient(create_app(runtime.config, runtime_config=runtime))
    return client, config_path, env_file
