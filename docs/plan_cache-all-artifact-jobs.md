# Apply Cache To All Artifact Jobs

## Goals
- Reuse completed successful artifacts for all user-facing HTTP artifact jobs.
- Coalesce duplicate in-flight artifact jobs so expensive work runs once.
- Keep collection-only jobs uncached because their main purpose is refreshing SQLite side effects.

## Checklist
- Generalize social-only cache lookup and coalesced render helpers in `HttpJobManager`.
- Add cache keys for PTR trading, SEC 13F, Adanos trendings, and statistic jobs.
- Re-render report artifacts from cached/coalesced `summary.json` where output mode or display limit is render-only.
- Copy statistic PNG artifacts for cached/coalesced statistic jobs because the chart output is exact-input cached.
- Keep `force_refresh=true` bypassing completed PTR/13F cache while allowing in-flight coalescing.
- Add focused HTTP job tests for cache hits, cache misses, render-only reuse, force-refresh bypass, and coalescing.
- Run pytest, config validation, and `git diff --check`.
