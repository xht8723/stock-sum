# Remove X/Reddit Playwright Collectors

## Goals
- Remove implemented X.com and Reddit Playwright collectors, CLI groups, config, source-specific storage, and tests.
- Keep generic Playwright dependency/config/package space for future non-X/non-Reddit scraping.
- Leave the project in a passing, API-first scaffold state without built-in source collectors.

## Checklist
- Delete X/Reddit collector modules and leave a generic Playwright package docstring.
- Remove X/Reddit CLI imports, command groups, config sections, and collector factory branches.
- Remove X/Reddit source-specific SQLite tables, mappers, media persistence reachability, and related tests.
- Update README and deployment docs to reflect an API-first scaffold with generic Playwright support.
- Run tests, config validation, CLI smoke checks, diff hygiene, and status.
