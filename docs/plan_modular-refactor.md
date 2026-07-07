# Stock-Sum Modular Refactor Plan

## Goals
- Refactor the largest mixed-responsibility files into smaller focused modules.
- Preserve current HTTP, CLI, Redbot, child-worker, and report-rendering behavior unless stale compatibility code is discovered.
- Keep existing public import paths working through compatibility wrappers where tests or runtime code depend on them.
- Verify each risky stage with focused tests before running the full suite.

## Checklist
- Split `stock_sum/api/jobs.py` into job models, validation, artifacts, cache/store helpers, and a coordinator `HttpJobManager`.
- Split `stock_sum/storage/sqlite.py` behind the existing `SQLiteStorageRepository` facade.
- Split `stock_sum/reports/presentation.py` into renderer dispatch, shared formatting, and report-family modules.
- Split `redbot_cogs/stocksum_report/stocksum_report.py` into client, validation, messages, cog, and a compatibility shim.
- Split `stock_sum/cli.py` helper logic into state, setup, and serialization modules while keeping the Typer command surface.
- Evaluate smaller extraction targets for statistics and Xpoz helpers without introducing avoidable import churn.
- Run focused tests after each stage, then full pytest, Docker Compose config validation, diff whitespace checks, and import smoke checks.
