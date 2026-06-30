# In-Flight Report Request Coalescing

## Goals
- Avoid duplicate Xpoz and LLM work when multiple users request the same report concurrently.
- Preserve existing completed-job cache behavior and Redbot behavior.
- Keep follower jobs as normal jobs with their own status, summary, and artifact.

## Checklist
- Add `server.coalesce_inflight_reports = true` to config models and TOML defaults/examples.
- Track active report jobs in `HttpJobManager` by the existing report cache key.
- Make the first matching job the leader and make later matching jobs wait for it.
- Render follower artifacts from the leader's `summary.json` after leader success.
- Mark follower failures clearly when the leader fails or has no summary.
- Add job metadata for coalesced jobs and waiting duration.
- Add HTTP job tests for concurrent coalescing, mixed output modes, option mismatch, leader failure, disabled coalescing, and retention.
- Run pytest, config validation, and `git diff --check`.
