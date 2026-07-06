# Add Adanos Trendings Report

## Goals
- Add standalone `/trendings` Discord command and matching HTTP job endpoints.
- Query Adanos Reddit Stocks and X/Twitter Stocks trending stock and sector endpoints.
- Store raw and normalized Adanos responses in SQLite.
- Render concise trendings reports with warnings only for failed Adanos requests.
- Treat missing `ADANOS_API_KEY` as a silent skip with an empty-state report.

## Implementation Checklist
- Add optional Adanos provider config, env example, setup prompt, and docs.
- Add Adanos API client with four endpoint calls using `X-API-Key`, `from`, `to`, and backend fetch `limit=100`.
- Add SQLite schema and repository methods for raw responses, normalized trending stocks, and normalized trending sectors.
- Add HTTP job options, job manager path, routes, worker operation, and rendering for `discord|html|markdown|text|json`.
- Add Redbot `/trendings from to limit` command and HTTP client wrapper.
- Add tests for config, client behavior, storage persistence, API/job behavior, rendering, and Redbot command behavior.
