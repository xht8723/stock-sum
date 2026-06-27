# Source List Config CLI Plan

## Goals
- Add readable source-list sections for long X user and subreddit lists.
- Add CLI helpers to add, delete, list, and show configured X users and subreddits.
- Keep existing collection commands stable: `stock-sum collect --collector x.<handle>` and `stock-sum collect --collector reddit.<subreddit>`.
- Preserve generic `[collectors.*.*]` support for future non-list collectors.

## Checklist
- Add typed source-list config models for X users and Reddit subreddits.
- Resolve collector IDs from both source lists and legacy/generic collector tables.
- Update default/example TOML to use `[[sources.x_users]]` and `[[sources.subreddits]]`.
- Add TOML writer helpers for X user and subreddit add/delete/list/show.
- Add CLI groups under `stock-sum config x-user` and `stock-sum config subreddit`.
- Update docs and tests for the new config shape and commands.
- Run tests, config validation, CLI smoke checks, and git hygiene checks.
