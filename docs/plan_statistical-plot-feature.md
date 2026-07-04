# Statistical Plot Feature

## Goals
- Add a `/statistic` Discord slash command that requests a stock-sum HTTP job and returns a PNG chart.
- Support `social` statistics from analyzed X/Reddit sentiment and `trading` statistics from House PTR disclosure rows.
- Keep the feature read-only against SQLite: no collection, refresh, or LLM work is triggered.

## Checklist
- Add Matplotlib dependency and a statistics module for querying, aggregation, and PNG rendering.
- Add repository methods for social ticker sentiment rows and House PTR trading statistic rows.
- Add `StatisticJobOptions`, `kind="statistic"`, worker support, and `POST /v1/statistics/jobs`.
- Add Redbot `/statistic` with validation and PNG file delivery.
- Add tests for storage queries, aggregation, API jobs, and Redbot command payloads.
- Verify with pytest, config validation, `git diff --check`, and a local PNG smoke test when dependencies are available.
