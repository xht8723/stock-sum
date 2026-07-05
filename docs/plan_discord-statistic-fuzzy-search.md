# Public Discord Commands And Fuzzy Search For `/statistic`

## Goals
- Remove user-selectable `private` options from public report/statistic slash commands.
- Add `/statistic fuzzy_search` with public emoji candidate selection.
- Add stock-sum API/storage support for social tag and trading asset fuzzy matches.

## Checklist
- Remove `private` options from `/socialreport`, `/tradingreport`, `/13freport`, and `/statistic`, while keeping secret/owner-only management responses ephemeral.
- Add `fuzzy_search` validation to `/statistic`; reject when both `ticker` and `fuzzy_search` are provided.
- Add `GET /v1/statistics/fuzzy-matches` returning up to five ready-to-use candidates.
- Add social `fuzzy_tag` and trading `asset_name` statistic filters.
- Implement Discord reaction selection with timeout edit and selection confirmation.
- Update tests for API, storage, and Redbot command behavior.
- Run full pytest, config validation, and `git diff --check`.
