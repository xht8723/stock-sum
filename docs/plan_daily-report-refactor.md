# Daily Report Refactor

## Goals

- Refactor `/daily` into a deterministic Discord brief built from existing JSON report outputs.
- Preserve standalone report presentation contracts, scheduling behavior, and PTR disclosure limits.
- Keep PTR disclosures as the final section and exclude them from the cover highlights.
- Deliver section-aware Discord messages without adding another LLM request.

## Implementation Checklist

- [x] Replace raw markdown concatenation with structured daily sections containing report kind, JSON payload, job status, warnings, timestamps, and errors.
- [x] Request JSON internally for trendings, social, and PTR jobs.
- [x] Render cover, market trends, high-priority social signals, and House PTR disclosures in that fixed order.
- [x] Show target date, actual generation time, source health, deterministic trend/social highlights, and trend/social ticker overlaps in the cover.
- [x] Keep PTR content and counts out of the cover.
- [x] Put trending changes before stock and sector lists while preserving the existing trend limits and ranking.
- [x] Show only high-importance daily social signals, sort them by confidence, display at most five, and state how many additional high-priority signals were omitted.
- [x] Add social JSON coverage metadata for generated time and per-source lookback windows.
- [x] Add a backward-compatible `allow_empty` trading-report option, defaulting to `false`, and use it only for `/daily`.
- [x] Keep every returned PTR disclosure, preserve the existing limit, label the rolling 24-hour filing window, and render a healthy empty state.
- [x] Return ordered Discord messages, split at item boundaries, and repeat section headings on continuation messages.
- [x] Preserve partial-failure delivery, once-per-UTC-date tracking, prestart scheduling, and anti-spam behavior.
- [x] Add focused tests for ordering, highlights, coverage, empty states, limits, splitting, and failure handling.
- [x] Run targeted tests, the full test suite, and `git diff --check`.
