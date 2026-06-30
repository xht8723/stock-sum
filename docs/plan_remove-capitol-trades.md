# Remove Capitol Trades Features

## Goals
- Remove Capitol Trades scraper code, report options, render sections, Discord options, tests, and active docs.
- Keep generic Playwright infrastructure for future website scrapers.
- Preserve existing uncommitted Xpoz, LLM chunking, storage, retention, and Discord work.

## Checklist
- Delete the Capitol Trades Playwright scraper and dedicated tests.
- Remove Capitol-related HTTP request fields, job options, cache-key inputs, and optional job phase.
- Remove CLI report-render Capitol options and imports.
- Remove Redbot `/report include_capitol_trades` option and request payload field.
- Remove Capitol rendering sections, helpers, CSS, and tests.
- Update active docs and tests so no active code path references Capitol Trades.
- Run pytest, config validation, `git diff --check`, and a final Capitol search.
