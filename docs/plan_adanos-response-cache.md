# Adanos 12-Hour Response Cache

## Goals

- Persist successful Adanos trendings responses per endpoint for 12 hours.
- Reuse only responses whose provider, endpoint, date window, and fetch limit match.
- Reuse successful endpoints from partial batches while retrying only missing or failed endpoints.
- Keep report rendering, public commands, and the existing artifact cache behavior unchanged.

## Implementation Checklist

- [x] Add a versioned, request-specific SQLite cache entry for each Adanos endpoint response.
- [x] Configure the response TTL through `providers.adanos.response_cache_ttl_seconds`, defaulting to 43,200 seconds.
- [x] Resolve all four endpoint requests before fetching and read fresh cached entries first.
- [x] Fetch only missing, expired, corrupt, or previously failed endpoints.
- [x] Cache successful responses, including empty lists, without extending timestamps on cache hits.
- [x] Combine cached and live responses in deterministic endpoint order and retain their original source jobs.
- [x] Avoid duplicating cached normalized history and exclude displayed source snapshots from trend comparisons.
- [x] Coordinate concurrent matching requests within the service process so followers recheck SQLite.
- [x] Add backward-compatible per-endpoint response-cache metadata to trendings JSON.
- [x] Cover persistence, expiry, partial failures, missing keys, corruption, history, and concurrency in tests.
- [x] Run targeted tests, the full pytest suite, and `git diff --check`.

## Locked Decisions

- The cache is request-specific rather than global.
- Successful endpoints are cached independently; failures are never cached.
- There is no per-job force-refresh option.
- Stale entries are not used as fallback data.
- Daily report, PTR behavior, and standalone report layouts remain unchanged.
