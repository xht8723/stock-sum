# Store Full API Returns And Preserve SQL Source History

## Goals
- Store all Xpoz API returns in SQLite before report-time filtering.
- Add a provider-response archive table for full Xpoz tool responses.
- Move 24-hour source filtering into SQL-backed payload reads.
- Preserve collected SQL source/history data permanently under retention cleanup.

## Checklist
- Remove X/Reddit lookback filtering from collectors while keeping fetch-cap warnings.
- Add raw provider API response archive models, repository methods, and SQLite schema/upserts.
- Capture Xpoz tool raw response text, request arguments, parsed rows, row count, and fetched timestamps.
- Persist captured provider responses from pipeline success and failure paths.
- Add normalized UTC timestamp columns to source tables and backfill existing rows best effort.
- Apply `since_posted_at` filtering in SQL queries.
- Update retention to exclude SQLite files and never prune source/history tables.
- Update tests and docs, then run full verification.
