# Reddit RSS Backend Collection Method

## Goals
- Add Reddit RSS as a backend-only alternate collection method.
- Keep Discord `/recent_posts` unchanged and defaulted to Xpoz.
- Persist all RSS listing posts returned by Reddit's observed max limit.
- Fetch RSS comments sequentially with bounded retries and per-post failure tolerance.
- Keep final social reports filtered from SQLite by the existing 24-hour lookback behavior.

## Implementation Checklist
- Add `reddit_method = "xpoz" | "rss"` to social report job options and HTTP request models.
- Add `[providers.reddit_rss]` config with RSS limits, retry delay, retry count, and a 20-hour total timeout.
- Implement a Reddit RSS collector that parses subreddit listing feeds and per-post comment feeds.
- Wire collector factory/pipeline selection so `reddit_method="rss"` only affects Reddit sources.
- Archive raw RSS feed responses through the existing provider response storage.
- Reuse existing Reddit raw tables and summary input SQL filtering.
- Add parser, collector, API/cache, and Redbot regression tests.
- Run full pytest, config validation, and `git diff --check`.
