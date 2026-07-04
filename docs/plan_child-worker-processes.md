# Child Worker Process Refactor Plan

## Goals
- Keep the stock-sum daemon as a lightweight job coordinator.
- Run every heavy report, collection, LLM, rendering, and retention-after-job task in a short-lived child process.
- Let the operating system reclaim worker memory when each heavy task exits.
- Preserve the existing HTTP API response shapes and Redbot-facing behavior.

## Checklist
- Add an internal worker entrypoint, `python -m stock_sum.worker`, that accepts a per-job request JSON and runs one operation.
- Split heavy execution logic out of `HttpJobManager` into worker-side functions while the parent keeps job creation, validation, cache/coalescing coordination, spawn/monitoring, and status lookup.
- Add a persisted job store helper for parent/child `status.json` updates.
- Add worker diagnostics to status payloads: `worker_pid`, `worker_started_at`, `worker_finished_at`, `worker_exit_code`, `worker_runtime_seconds`, and `worker_mode`.
- Use one subprocess per heavy operation; do not add a worker pool, external queue, hard memory caps, or timeouts in this pass.
- Mark stale `queued` and `running` persisted jobs failed on daemon startup.
- Delegate heavy CLI commands through the same worker path: `run-report`, `collect`, `llm summarize`, and `llm analyze`.
- Keep lightweight management/config/profile/source commands in-process.
- Change House PTR defaults to `download_concurrency = 1` and `parse_concurrency = 1`.
- Add focused tests for subprocess runner behavior, worker entrypoint behavior, stale job recovery, HTTP compatibility, and CLI delegation.
- Run focused tests, then full `pytest`.
