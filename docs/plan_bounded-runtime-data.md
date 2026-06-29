# Bounded Runtime Data Storage

## Goals
- Prevent app-managed runtime data from growing indefinitely.
- Apply a default 2GB total cap across HTTP job artifacts, downloaded images, and SQLite storage.
- Run cleanup after report/collection pipeline writes finish.
- Keep cleanup best-effort so it does not fail an otherwise successful report.

## Checklist
- Add retention config with defaults: enabled, max total bytes, prune-after-pipeline, and SQLite vacuum.
- Implement a data retention service that measures managed runtime paths and prunes oldest data.
- Delete HTTP job directories first, downloaded media next, and SQLite history last.
- Keep downloaded media files and `downloaded_media` rows in sync.
- Add CLI commands for retention status and dry-run/apply pruning.
- Hook cleanup after HTTP report jobs, HTTP collect jobs, CLI collection/report runs, and image-download payload builds.
- Add unit and integration tests for measurement, pruning order, SQLite cleanup, disabled retention, and post-run hooks.
- Run full pytest, config validation, and `git diff --check`.
