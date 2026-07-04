# 1GB RAM Memory-Risk Mitigation

## Goals
- Bound daemon process memory for a 1GB RAM VM.
- Keep HTTP job status compatible through persisted `status.json` files.
- Reduce peak report memory when SQLite history grows.
- Document low-memory deployment settings.

## Checklist
- Add bounded in-memory HTTP job pruning using `server.job_retention_hours` and a new `server.max_in_memory_jobs`.
- Preserve queued, running, current, and in-flight/coalesced jobs while evicting old completed records.
- Keep `get_job()` able to reload evicted jobs from disk.
- Add memory status fields to job and retention status payloads.
- Add report input caps through a non-breaking `[report_input]` config section.
- Apply X/Reddit source caps and Reddit comment caps in summary input building.
- Update defaults, examples, and deployment docs for 1GB VM operation.
- Add focused tests for job eviction and summary input caps.
