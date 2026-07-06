# Single Source Of Truth For User Query Limits

## Goals
- Move user-facing report query limit defaults into stock-sum.
- Keep Discord from defaulting or max-capping report query limits.
- Preserve technical safety caps such as Discord chunk size and fuzzy match choices.

## Checklist
- Keep trading report server-owned default limit at `100`.
- Move 13F report default limit to stock-sum job options.
- Remove 13F API/Discord max limit of `100`.
- Keep 13F limit positive-only validation.
- Update Redbot docs and tests.
- Verify with pytest, config validation, and `git diff --check`.
