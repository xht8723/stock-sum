# Grouped Presentation Layout

## Goals
- Reformat final reports into source-first sections: X by user and Reddit by post/comment sentiment.
- Adjust the LLM prompt output schema so future summaries produce the grouped structure directly.
- Keep renderer compatibility with older flat `x_signals` / `reddit_signals` responses.
- Preserve media linking under the matching post/source card.

## Checklist
- Update prompt schema to request `x_reports` and `reddit_report`.
- Render HTML, Markdown, and text using the requested heading hierarchy.
- Keep fallback rendering for old response JSON.
- Add tests for grouped X/Reddit layout and media links.
- Run pytest, config validation, and diff check.
