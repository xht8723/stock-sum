# SEC 13F Dataset Source And `/13freport`

## Goals
- Add an official SEC Form 13F dataset collector using the latest quarterly ZIP from the SEC data page.
- Store parsed TSV rows in source-specific SQLite tables with queryable normalized holding fields.
- Add deterministic 13F report generation through HTTP and Redbot `/13freport`, without LLM usage.
- Run a module-only real smoke test that downloads/parses/query-renders a narrow 13F report.

## Checklist
- Add SEC 13F config, collector factory wiring, and source constants.
- Implement latest ZIP discovery, ZIP download, TSV parsing, and RawItem generation.
- Add SQLite schema/upserts/read methods for 13F datasets and holdings.
- Add HTTP job options/endpoints and report manager flow for `/v1/13f-reports/jobs/{mode}`.
- Add presentation rendering for SEC 13F Holdings in HTML, Markdown, Discord Markdown, text, and JSON.
- Add Redbot HTTP client and `/13freport` slash command.
- Add focused unit tests for discovery/parsing/storage/query/API/renderer/Redbot behavior.
- Run pytest, config validation, diff check, and a real module-only 13F smoke test.
