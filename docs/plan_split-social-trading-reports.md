# Split Social And Trading Reports

## Goals
- Replace the Discord `/report` command with `/socialreport` for X/Reddit LLM-backed reports.
- Add `/tradingreport` for official House PTR disclosure reports without LLM intervention.
- Split HTTP report jobs into social-only and trading-only paths.
- Add House PTR query filters by fuzzy name and transaction date/timeline.
- Improve House PTR name/date/action normalization for reliable querying and rendering.

## Checklist
- Add House PTR refresh TTL and normalized storage/query fields.
- Refactor repository methods and SQL initialization/backfill for House PTR filtering.
- Split report job options and endpoints into social report and trading report flows.
- Add deterministic trading report rendering for HTML, Markdown, Discord Markdown, and text.
- Remove House PTR from social report rendering and keep compatibility report endpoints as social aliases.
- Rename Redbot `/report` to `/socialreport` and add `/tradingreport`.
- Update tests for API jobs, storage/parser behavior, renderers, and Redbot commands.
- Run full pytest, config validation, and diff hygiene checks.
