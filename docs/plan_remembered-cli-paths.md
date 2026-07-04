# Use Remembered Setup Paths Across Stock-Sum CLI

## Summary
Make all CLI commands that need the active config, env file, or data location use the remembered setup state by default. After `stock-sum setup init`, users should be able to run commands like `stock-sum secrets set XPOZ_API_KEY`, `stock-sum daemon`, `stock-sum setup check`, and source/profile commands without repeatedly passing `/opt/stock-sum/app/config.toml` or `/etc/stock-sum/stock-sum.env`.

## Key Changes
- Add shared path resolution helpers in the CLI:
  - `resolve_config_path(explicit=None)` returns explicit `--config` if provided, else remembered setup config, else current default.
  - `resolve_env_file_path(explicit=None)` returns explicit `--env-file` if provided, else remembered setup env file, else `.env`.
  - `resolve_data_dir_path(explicit=None)` returns explicit `--data-dir` if provided, else remembered setup data dir, else `data`.
  - Explicit CLI flags always override remembered setup state.
- Update CLI command defaults:
  - Change config/env options from concrete `Path(...)` defaults to `None` where remembered setup should apply.
  - Apply remembered config path to daemon, collection/report, payload, retention, database, LLM, config sync, profile/source, and House PTR commands.
  - Apply remembered env file path to daemon, setup check, and secrets commands.
- Keep setup commands predictable:
  - `setup init` still writes remembered state after successful setup.
  - `setup check` uses remembered config/env by default.
  - `setup reset` keeps deleting remembered active paths, with explicit options overriding.
- Improve help text and docs:
  - Explain that `--config` and `--env-file` default to remembered setup paths.
  - Document that VM users can run `stock-sum secrets set XPOZ_API_KEY` after setup without specifying `--env-file`.

## Test Plan
- CLI tests for remembered env/config defaults, explicit override precedence, and no-state fallback behavior.
- Runtime tests that `daemon` uses remembered config/env paths by default.
- Full pytest, example config validation, and `git diff --check`.

## Assumptions
- The setup state file remains `.stock-sum-state.json` in the current working directory unless `--state-file` is passed.
- This is a CLI usability change only; HTTP management endpoints already use active paths passed at daemon startup.
- No config schema or database migration is needed.
