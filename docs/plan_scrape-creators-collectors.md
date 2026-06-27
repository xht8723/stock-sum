# Scrape Creators X/Reddit Collector Plan

## Goals
- Implement Scrape Creators as the first API collector provider.
- Keep real API keys in environment variables only.
- Add source-specific X and Reddit collectors, mappers, and SQLite tables.
- Persist post/comment media metadata as remote URLs and raw JSON, not downloaded files.

## Checklist
- Add provider config for Scrape Creators with `api_key_env`, `base_url`, and timeout fields.
- Add a shared `ScrapeCreatorsClient` using `httpx.AsyncClient` and the `x-api-key` header.
- Add X user-tweets and Reddit subreddit collectors under the API collector namespace.
- Register Scrape Creators collector kinds in the collector factory.
- Add source-specific SQLite schema and mappers for X posts/media and Reddit posts/comments/media.
- Update default/example TOML with disabled example collectors and no default profile references.
- Update README/deployment docs with API key placement and usage commands.
- Add unit tests for client, collectors, mappers, SQLite persistence, config, and CLI.
- Run pytest, config validation, CLI smoke checks, and git hygiene checks.
