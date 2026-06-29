# Stock-Sum Management API And Discord Slash Commands

## Goals
- Add a local management API for profiles, sources, LLM settings, secrets, setup checks, collection jobs, and retention.
- Add Redbot slash commands that map to the management API.
- Keep `/report` as the simple report command.
- Make config changes hot-reload into the daemon without a restart.
- Keep mutating and secret-related Discord commands owner-only.

## Checklist
- Add a runtime config manager that tracks config/env paths, writes TOML/env changes, reloads config, and refreshes future job-manager dependencies.
- Add management API endpoints under `/v1` for profiles, sources, LLM, secrets, setup check, retention, and collector-specific collection.
- Keep management endpoints loopback-only by default while preserving the existing IP blacklist.
- Extend the Redbot cog with a `/stocksum` slash-command group for operational commands.
- Add owner checks for mutating Redbot commands and ephemeral handling for secrets.
- Add API and Redbot tests for CRUD, redaction, hot reload, and command routing.
- Run full pytest, config validation, and `git diff --check`.
