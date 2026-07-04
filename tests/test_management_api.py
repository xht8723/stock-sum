"""Management API tests."""

from pathlib import Path

from fastapi.testclient import TestClient

from stock_sum.api.app import create_app
from stock_sum.api.runtime_config import RuntimeConfigManager
from stock_sum.config.loader import load_config
from stock_sum.config.writer import write_default_config


def test_profile_and_source_management_hot_reloads_config(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)

    create_profile = client.post(
        "/v1/profiles",
        json={"name": "tech", "collector_ids": [], "overwrite": False},
    )
    assert create_profile.status_code == 200

    add_x = client.post(
        "/v1/sources/x-users",
        json={"handle": "newuser", "profile": "tech", "limit": 50, "lookback_hours": 12, "enabled": True},
    )
    assert add_x.status_code == 200
    assert add_x.json()["collector_id"] == "x.newuser"

    profile = client.get("/v1/profiles/tech")
    assert profile.status_code == 200
    assert profile.json()["profile"]["collector_ids"] == ["x.newuser"]

    config = load_config(config_path)
    assert "tech" in config.reports
    assert config.reports["tech"].collector_ids == ["x.newuser"]
    assert any(
        source.handle == "newuser" and source.enabled and source.limit == 50 and source.lookback_hours == 12
        for source in config.sources.x_users
    )

    add_reddit = client.post("/v1/sources/subreddits", json={"subreddit": "stocks", "profile": "tech"})
    assert add_reddit.status_code == 200
    config = load_config(config_path)
    reddit_source = next(source for source in config.sources.subreddits if source.subreddit == "stocks")
    assert reddit_source.include_comments is True
    assert reddit_source.comments_per_post == 10


def test_reddit_source_delete_updates_profile(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)

    response = client.delete("/v1/sources/subreddits/wallstreetbets?profile=default")

    assert response.status_code == 200
    config = load_config(config_path)
    assert all(source.subreddit != "wallstreetbets" for source in config.sources.subreddits)
    assert "reddit.wallstreetbets" not in config.reports["default"].collector_ids


def test_house_ptr_source_management_hot_reloads_config(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)

    show = client.get("/v1/sources/house-ptr")
    patch = client.patch(
        "/v1/sources/house-ptr",
        json={"enabled": False, "year": 2025, "profile": "default"},
    )

    assert show.status_code == 200
    assert show.json()["house_ptr"]["enabled"] is True
    assert patch.status_code == 200
    assert patch.json()["collector_id"] == "house.ptr"

    config = load_config(config_path)
    assert config.sources.house_ptr.enabled is False
    assert config.sources.house_ptr.year == 2025
    assert "house.ptr" not in config.reports["default"].collector_ids


def test_llm_management_lists_and_selects_provider(tmp_path) -> None:
    client, config_path, _env_file = _management_client(tmp_path)

    providers = client.get("/v1/llm/providers")
    assert providers.status_code == 200
    assert providers.json()["providers"][0]["provider_id"] == "deepseek"

    patch = client.patch("/v1/llm/config", json={"provider": "deepseek", "model": "deepseek-v4-flash"})
    assert patch.status_code == 200
    assert patch.json()["llm"]["provider"] == "deepseek"
    assert load_config(config_path).llm.model == "deepseek-v4-flash"


def test_secret_management_is_write_only(tmp_path) -> None:
    client, _config_path, env_file = _management_client(tmp_path)

    put_response = client.put("/v1/secrets/TEST_SECRET", json={"value": "super-secret"})
    list_response = client.get("/v1/secrets")
    env_text_after_put = env_file.read_text(encoding="utf-8")
    delete_response = client.delete("/v1/secrets/TEST_SECRET")

    assert put_response.status_code == 200
    assert "super-secret" not in put_response.text
    assert list_response.status_code == 200
    assert "TEST_SECRET" in list_response.json()["secrets"]
    assert "super-secret" not in list_response.text
    assert "TEST_SECRET=super-secret" in env_text_after_put
    assert delete_response.status_code == 200
    assert "TEST_SECRET" not in env_file.read_text(encoding="utf-8")


def test_setup_check_and_retention_endpoints(tmp_path) -> None:
    client, _config_path, _env_file = _management_client(tmp_path)

    setup = client.get("/v1/setup/check")
    retention = client.get("/v1/retention/status")
    prune = client.post("/v1/retention/prune", json={"dry_run": True})

    assert setup.status_code == 200
    assert setup.json()["status"] in {"ok", "issues"}
    assert retention.status_code == 200
    assert "bytes_before" in retention.json()
    assert retention.json()["in_memory_jobs"] == 0
    assert retention.json()["max_in_memory_jobs"] == 200
    assert prune.status_code == 200
    assert prune.json()["dry_run"] is True
    assert prune.json()["evicted_in_memory_jobs"] == 0


def _management_client(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    env_file = tmp_path / ".env"
    write_default_config(config_path, overwrite=True)
    env_file.write_text("XPOZ_API_KEY=x\nDEEPSEEK_API_KEY=d\n", encoding="utf-8")
    runtime = RuntimeConfigManager.from_paths(config_path, env_file)
    client = TestClient(create_app(runtime.config, runtime_config=runtime))
    return client, config_path, env_file
