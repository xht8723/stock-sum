# House PTR Disclosure Source

## Goals
- Add an official House PTR disclosure source using the current-year ZIP and PTR PDF URL pattern.
- Store filings, extracted PDF tables, and normalized trade rows in SQLite.
- Render recent official trading disclosures in final reports without sending them to the LLM.
- Allow House-only reports to succeed when no X/Reddit social data is available.

## Checklist
- Add PDF extraction dependency and House PTR config/source models.
- Add a modular collector that downloads the House ZIP, filters XML filing type `P`, fetches PTR PDFs, and extracts table rows concurrently.
- Add source-specific SQLite tables, repository methods, and mapper support.
- Register `house.ptr` in default config and collector factory.
- Add report orchestration so House data is attached to `summary.json` and empty-social reports can still succeed when House rows exist.
- Render "Official Trading Disclosures" in HTML, Markdown, Discord Markdown, and text.
- Add tests for collector parsing, storage, pipeline/report behavior, and renderer output.
- Run full pytest, config validation, and diff checks.
