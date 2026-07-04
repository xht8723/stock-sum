# Contract-Reset Codebase Cleanup

## Goals
- Convert `stock-sum` from a scaffold-plus-compatibility repo into an honest API-first service surface.
- Preserve implemented Xpoz X/Reddit collection, House PTR, SEC 13F, SQLite persistence, LLM analysis, presentation rendering, HTTP jobs, Redbot commands, retention, and setup/config management.
- Remove stale or misleading Playwright, scheduler, email/WhatsApp delivery, legacy renderer, unused registry/database, compatibility-route, tracked lock-file, and obsolete plan-doc surfaces.

## Checklist
- Remove compatibility endpoints: top-level `POST /reports/{profile}/run` and `/v1/reports/{profile}/jobs[/mode]`.
- Simplify report profile config to `collector_ids` only; remove schedule/timezone/delivery fields from models, TOML, writer, CLI, API requests, and tests.
- Remove delivery config and modules.
- Remove Playwright config, dependency, Docker install step, collector module, and tests.
- Remove scheduler scaffolding and daemon wiring.
- Remove legacy report renderer protocols, unused collector registry/database helper, unused domain types, and unimplemented storage protocol methods.
- Update README and deployment docs for canonical Xpoz-only API behavior.
- Delete obsolete `docs/plan_*.md` files except this plan.
- Remove tracked empty `downloaded_files/driver_fixing.lock`.
- Safely remove ignored local runtime artifacts under `.pytest_cache/`, `stock_sum.egg-info/`, `temp/`, and generated `data/` after path checks.
- Run full tests and hygiene checks: `pytest -q`, targeted `rg`, `git diff --check`, and `docker compose config`.
