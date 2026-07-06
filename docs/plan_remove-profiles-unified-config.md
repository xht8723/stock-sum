# Remove Profiles And Use One Unified Configuration

## Goals
- Remove user-facing and internal report profiles.
- Use one global enabled social source set for X and Reddit.
- Keep trading and SEC 13F as separate report products.
- Reset schema expectations instead of preserving profile-scoped SQLite history.

## Checklist
- Remove profile config models, TOML sections, writer helpers, CLI commands, API routes, and Redbot command groups.
- Replace profile-scoped social report and collect endpoints with unified endpoints.
- Refactor pipeline, summary input building, LLM analysis, storage, and statistics to use the global social context instead of a profile column/filter.
- Update source management so add/delete/edit only changes source lists.
- Update docs and tests to reflect profile-free commands and APIs.
- Run full pytest, config validation, and `git diff --check`.
