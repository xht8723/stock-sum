# 24-Hour X And Reddit Collection Window

## Goals
- Change X and Reddit collection from "latest N posts" to best-effort collection of posts within the last 24 hours.
- Keep Xpoz request limits as fetch safety caps.
- Prevent older stored SQLite rows from leaking into new summary inputs and reports.
- Surface warnings when a fetch cap may hide additional in-window posts.

## Checklist
- Add `lookback_hours = 24` to source and collector config models and defaults.
- Raise default X/Reddit fetch caps from 10 to 100.
- Thread `lookback_hours` through config writer, CLI, management API, and Redbot source-add commands.
- Filter Xpoz X and Reddit collectors by parsed post timestamps and generate cap warnings.
- Propagate collector warnings through the pipeline's existing warning model.
- Extend repository reads and summary input builder to filter stored rows by configured source windows.
- Add tests for collector filtering, repository/payload filtering, config/API/CLI/Discord surfaces, and warning propagation.
- Run full pytest, config validation, and `git diff --check`.
