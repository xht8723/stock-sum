# Fail-Safe Report Pipeline Plan

## Goals
- Keep report jobs successful when optional sections fail but usable social data exists.
- Record structured warnings for failed sections in job status, summary JSON, and rendered reports.
- Continue profile collection when one collector fails, while still failing truly unusable reports.

## Checklist
- Add a shared pipeline warning data shape and include warnings in collection results and HTTP job records.
- Make profile collection collect all configured collectors, recording per-collector failures instead of aborting the profile.
- Add a minimum social-data gate after payload construction and before LLM summarization.
- Treat Capitol Trades scrape failures as recoverable report warnings.
- Render concise unavailable-section warnings in HTML, Markdown, Discord Markdown, and text modes.
- Update Redbot to surface warning counts in completion messages without failing successful jobs.
- Add tests for partial collection, all-collector failure, recoverable Capitol Trades failure, no-data fatal failure, renderer warnings, and Redbot warning behavior.
