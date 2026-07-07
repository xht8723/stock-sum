# Daily Discord DM Report

## Goals
- Add Redbot-side daily report subscriptions with `/daily time:HH:MM` and `/cancel_daily`.
- Keep stock-sum API unchanged; scheduled delivery is owned by the Discord cog.
- Run daily bundles sequentially in this order: trendings, recent posts, PTR search for the last 24 hours.
- Send one combined DM after all three jobs finish, with warning sections for failed jobs.

## Checklist
- Add daily subscription persistence using Redbot `Config`, with a local fallback for tests.
- Add UTC time validation and slash commands for setting/canceling a subscription.
- Add a cog-owned background loop that checks due subscriptions once per minute and avoids duplicate sends per UTC date.
- Add daily bundle rendering that keeps report sections separated and catches each job failure independently.
- Add unit tests for command contracts, validation, persistence, due checks, sequential execution, warnings, and DM output.
- Update Redbot help/README and `info.json` end-user data statement.
- Run focused Redbot tests, the full suite, and `git diff --check`.
