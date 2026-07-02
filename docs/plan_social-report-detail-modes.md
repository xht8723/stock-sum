# Social Report Detail Modes

## Goals
- Add social-report detail modes: `minimum`, `medium`, and `full`.
- Default social report output to `minimum`, rendering only high-importance posts.
- Keep collection, LLM analysis, storage, and trading reports unchanged.

## Checklist
- Add a social report detail option to HTTP social report requests and job options.
- Pass detail through social report rendering without including it in expensive cache/coalescing keys.
- Filter renderer importance buckets by detail level for HTML, Markdown, Discord Markdown, and text.
- Add `/socialreport detail:` choices to the Redbot cog and pass the value to stock-sum.
- Add CLI render support for `--detail`.
- Update active docs and tests for default minimum behavior and explicit full/medium output.
