# Final Presentation Renderer Module

## Goals
- Convert `stock-sum llm summarize` JSON output into final presentation files.
- Support `html`, `markdown`, and `text` modes through one renderer.
- Preserve source attribution, links, confidence labels, risks, media observations, and metadata.
- Add a CLI command for file-based rendering.
- Verify with tests and real temp artifacts.

## Checklist
- Add a presentation renderer under `stock_sum/reports/`.
- Add safe HTML escaping and graceful handling for missing sections.
- Add `stock-sum report render --input ... --mode ... --output ...`.
- Add renderer and CLI tests for all modes and malformed input.
- Run pytest, config validation, `git diff --check`.
- Render live DeepSeek response to `C:\tmp\stock-sum-report.html`, `.md`, and `.txt`.
