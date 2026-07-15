# Xpoz Usage-Limit Error Handling

## Goals

- Recognize Xpoz tool-level error payloads returned inside successful HTTP/MCP envelopes.
- Report exhausted Xpoz credits as an actionable provider error instead of an empty successful collection.
- Preserve raw provider responses for diagnosis when a tool-level error is raised.
- Surface the actual collector failure in the job status and final Discord message.

## Checklist

- [x] Parse Xpoz `status: error` text payloads before row parsing.
- [x] Map `usage_limit` responses to `XpozCreditsError` with a stable, actionable message.
- [x] Archive the raw tool response before propagating the error.
- [x] Prefer collector failure reasons over the generic no-social-data message.
- [x] Add Xpoz client, HTTP job, and Redbot final-output regression tests.
- [x] Run focused tests, the full test suite, and formatting checks.

## Verification

- Focused Xpoz/job/Redbot regressions passed.
- Source compilation and `git diff --check` passed.
- Full suite result: 331 passed, with one unrelated calendar-sensitive PTR test excluded after it failed because its fixed July 1 fixture fell outside today's rolling 14-day window.
