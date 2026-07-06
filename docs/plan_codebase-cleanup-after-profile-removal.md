# Codebase Cleanup After Profile Removal

## Goals
- Remove stale profile/refactor leftovers from active code, tests, docs, and examples.
- Rename misleading report/collection internals so collection-only behavior is not called report execution.
- Remove SQLite migration/backfill code that conflicts with the reset-schema policy.
- Keep public HTTP endpoints stable and preserve current behavior.

## Checklist
- Replace `ReportPipeline.run_report()` with a collection-specific method and update workers, jobs, and tests.
- Remove negative compatibility tests for deleted profile-era endpoints.
- Update sample payload JSON files from `profile` to `report_type`.
- Remove additive SQLite schema update/backfill helpers and old-schema migration tests.
- Run repo-wide stale-reference scans.
- Verify with full pytest, config validation, and `git diff --check`.
