# X Playwright Collector

## Goals

- Implement the first real Playwright collector for X.com user timelines.
- Support headless scraping with a persistent profile.
- Provide a headed manual login flow that remembers the browser profile for later headless runs.
- Add CLI commands for scraping, login, and profile status.
- Validate against `@aleabitoreddit` for 10 recent posts when X allows access.

## Checklist

- Extend Playwright config with X-specific settings.
- Implement X parsing helpers, login-wall detection, post deduplication, and timeline scrolling.
- Add CLI commands: `stock-sum x scrape`, `stock-sum x login`, and `stock-sum x status`.
- Add unit tests for URL/status parsing, filtering, config validation, and CLI help.
- Run pytest and attempt a live scrape without storing credentials.
