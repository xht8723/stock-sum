# Daily Report Debounce

## Goals
- Start daily report jobs 30 minutes before the user's configured UTC time.
- If a user sets a time inside the next 30 minutes, run the daily bundle immediately.
- Preserve once-per-target-UTC-date delivery and avoid double-runs around midnight.

## Checklist
- Add a 30-minute daily pre-start window constant.
- Change daily due calculation to compute the target scheduled date/time, not just compare current `HH:MM`.
- Mark `last_sent_utc_date` using the target scheduled date, including when the run starts on the previous UTC date.
- Add Redbot tests for pre-start, immediate within-window start after registration, and after-midnight rollover behavior.
- Run focused Redbot tests, full tests, and `git diff --check`.
