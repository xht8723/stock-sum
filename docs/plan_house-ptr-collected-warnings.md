# House PTR Collection-Time and Photo-Scan Fix

## Goals

- Define newly discovered House PTR filings by their immutable `collected_at` timestamp.
- Preserve and display filings even when no transaction rows can be extracted.
- Classify PDFs with no extracted text or tables as photo scanned and link to the official document.
- Add versioned, transactional SQLite migrations without breaking existing filing-date filters or transaction responses.

## Implementation Checklist

- [ ] Add a transactional migration runner and `schema_migrations` table.
- [ ] Add House PTR extraction warning/metadata storage, a `collected_at` index, and legacy backfill.
- [ ] Preserve the first `collected_at` value during filing upserts.
- [ ] Classify filing extraction as `succeeded`, `photo_scanned`, `unparsed`, or `failed`.
- [ ] Skip already processed successful, photo-scanned, and unparsed filings while retrying failures.
- [ ] Add `collected_days` to the trading-report job contract and use it for daily PTR reports.
- [ ] Return filing-level results independently of extracted transaction rows.
- [ ] Render photo-scanned and unparsed filings with warnings and official document links.
- [ ] Bypass stale report artifacts when an explicit refresh is requested.
- [ ] Add migration, persistence, collection, API, cache, and renderer coverage.
- [ ] Run the full local test suite.
- [ ] Back up the VM database, deploy, restart, and smoke-test known filings.

## Fixed Behavior

- `photo_scanned` means the PDF was downloaded successfully and both extracted text and detected tables were empty.
- The rendered photo-scan warning is exactly: **The filing is photo scanned**.
- `unparsed` means some text or table content existed but no valid transactions could be normalized.
- No OCR, LLM, vision provider, provider configuration, or future LLM persistence hooks are included.
