# Initial Architecture Setup

## Goals

- Initialize Git for the project without creating a commit.
- Create a Python 3.12 package scaffold for a modular trading-report service.
- Establish protocol interfaces for collectors, LLMs, reports, delivery, scheduler, and storage.
- Add TOML configuration models and readable defaults/examples.
- Add cache-first models.dev catalog handling with a daily refresh cadence.
- Add CLI, HTTP, Docker, and test scaffolding without implementing business logic.

## Checklist

- Add Python-focused project metadata, ignore rules, and Docker files.
- Create the `stock_sum` package with module skeletons and executable entrypoints.
- Define Pydantic config models and TOML loader/writer stubs.
- Define collector, LLM, report, delivery, scheduler, storage, and pipeline protocols/stubs.
- Add FastAPI routes for health, manual report trigger, and redacted config.
- Add tests for imports, config shape, CLI help, protocol shape, HTTP health, and model catalog cache behavior.
- Initialize Git and verify scaffolded files are visible in `git status --short`.
