# Multi-Call LLM Analysis Pipeline

## Goals
- Replace one large LLM summary call with multiple bounded analysis calls.
- Analyze Reddit per post with comment/reply sentiment.
- Analyze X in handle-grouped post chunks.
- Persist post/comment sentiments, tags, summaries, and raw LLM response metadata to SQLite.
- Render final reports deterministically from stored analysis rows and statistics.

## Implementation Checklist
- Add LLM analysis chunk models, prompts, parsing, and a service that can call provider-neutral JSON completion.
- Add DeepSeek JSON completion support while keeping the legacy `summarize()` debug path.
- Add SQLite analysis tables and repository read/write methods for X post, Reddit post, and Reddit comment analyses.
- Wire HTTP report jobs to collect, build payload, run chunked analysis, persist results, build deterministic `summary.json`, and render.
- Add a debug CLI path for chunked analysis.
- Update presentation rendering for tags, main post sentiment, and Reddit comment sentiment counts.
- Add unit and pipeline tests for chunking, parsing, persistence, partial chunk failures, deterministic rendering, and existing compatibility.
