# First-Run Setup And User-Friendly Configuration Plan

## Goals
- Add an interactive first-run setup flow without making `stock-sum daemon` interactive.
- Persist secrets in env files while keeping TOML secret-free.
- Add safe CLI commands for env-file secret management.
- Add dynamic LLM provider metadata and provider listing.
- Add setup validation for config, required secrets, and writable runtime paths.

## Checklist
- Add provider descriptors and `stock-sum llm providers`.
- Add env-file read/write helpers with restrictive permissions where supported.
- Add `stock-sum setup init` and `stock-sum setup check`.
- Add `stock-sum secrets set/list/remove`.
- Validate daemon required env vars before starting.
- Update README/deployment docs.
- Add tests for setup, secrets, provider listing, and daemon validation.
