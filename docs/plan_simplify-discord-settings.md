# Simplify Discord Commands And Source Settings

## Goals
- Remove the broad Discord `/stocksum` management surface and matching local management endpoints that only supported it.
- Keep a small `/settings` Discord command group for X and Reddit source list/add/remove.
- Make Discord report commands always use Discord markdown output.
- Default `/tradingreport` to 100 rows.

## Checklist
- Remove `/stocksum` Redbot command groups and add `/settings list|add-x|remove-x|add-reddit|remove-reddit`.
- Remove `format` options from `/socialreport`, `/tradingreport`, and `/13freport`; hardcode `discord`.
- Keep `/statistic` unchanged.
- Remove management API endpoints for collect, House source settings, LLM config, secrets, setup check, and retention.
- Keep source endpoints for X and Reddit.
- Update docs and tests for the smaller Discord/API surface.
- Verify with pytest, config validation, and `git diff --check`.
