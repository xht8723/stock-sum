# Setup Reset Command Plan

## Goals
- Add a guarded reset command for returning a local stock-sum install to a clean first-run state.
- Require prominent warnings and two confirmations by default.
- Remove only explicitly configured local state paths.

## Checklist
- Add `stock-sum setup reset`.
- Delete config file, env file, and data directory when present.
- Require `yes` confirmation plus typing `RESET`, unless `--yes` is passed.
- Add tests for confirmed reset and cancelled reset.
- Update README with the reset command warning.
