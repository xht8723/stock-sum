# Add Backend-Only X RSS Method via Nitter

## Goals
- Add a backend-only X collection method using Nitter RSS feeds such as `https://nitter.net/aleabitoreddit/rss`.
- Store RSS-derived X posts in the existing X raw tables and archive successful raw RSS responses.
- Keep Xpoz as the default X collection method and do not expose the RSS method in Discord commands.

## Checklist
- Add `[providers.nitter_rss]` config and typed provider settings.
- Add an RSS collector for X/Nitter feeds with sequential fetch, 429/5xx retry, malformed-item warnings, and raw response archive capture.
- Wire `x_method = "xpoz" | "rss"` through HTTP social report options, cache keys, pipeline collection, and collector factory.
- Map `x_rss_user_timeline` into existing `raw_x_posts` / `raw_x_post_media`.
- Add tests for parsing, retry behavior, factory selection, storage mapping, HTTP cache key behavior, and Discord command non-exposure.
- Run full pytest, config validation, and `git diff --check`.
