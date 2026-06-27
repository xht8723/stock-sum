# Storage And Collection Pipeline

## Goals

- Add durable SQLite storage for collection runs and source-specific raw data.
- Use source-specific tables for X and Reddit rather than a generic API table.
- Add source-aware mappers from `RawItem` into storage rows.
- Add config-driven collector construction and collection CLI commands.
- Make `ReportPipeline.run_report()` perform collection and persistence as its first real phase.
- Verify with repository, CLI, and pipeline tests, then commit.

## Checklist

- Create SQLite schema initialization for shared run/index tables plus `raw_x_posts` and `raw_reddit_posts`.
- Implement `SQLiteStorageRepository` with run lifecycle and raw item upsert methods.
- Add mapper functions for X and Reddit source types and clear errors for unsupported source types.
- Add collector factory support for configured X collectors.
- Add `stock-sum collect --collector ...` and `stock-sum collect --profile ...`.
- Wire pipeline collection persistence for configured profiles.
- Add tests for schema, dedupe, mapper errors, CLI help, and pipeline behavior.
