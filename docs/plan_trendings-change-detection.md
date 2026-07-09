# Add Trendings Change Detection

## Goals
- Extend Adanos trendings reports with a notable changes section based on prior SQL history.
- Add server-side defaults for comparison window and thresholds.
- Keep Discord simple while exposing optional slash-command parameters.
- Preserve Adanos raw/normalized storage and display-only trendings limits.

## Checklist
- Add trendings job/request options for `days`, `mentions_change_pct`, `sentiment_change_pct`, and `minimum_mentions`.
- Add repository support for reading latest prior Adanos stock rows per platform/ticker within the comparison window.
- Build change detection after current Adanos rows are persisted and before rendering.
- Render `Trending changes` in Discord, Markdown, HTML, text, and JSON summary output.
- Include new options in trendings cache/coalescing keys, excluding display limit.
- Update Redbot `/trendings` payload and validation.
- Add tests for storage lookup, change detection, API/Discord payloads, renderer behavior, and cache keys.
