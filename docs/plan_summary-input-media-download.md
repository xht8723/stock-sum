# Repository Read, Image Download, And Summary Input Payload

## Goals
- Add source-specific repository read methods for collected X and Reddit data.
- Add image download support with deterministic local files and SQLite metadata.
- Define a clean LLM-ready `SummaryInput` payload grouped by source type.
- Add a CLI command to build the payload and write JSON.
- Generate a real sample payload under `docs/examples/`.

## Checklist
- Add media download config fields and defaults.
- Add stored row models and summary input models.
- Add SQLite read methods and `downloaded_media` persistence.
- Implement an image-only downloader with content-type, byte, hash, and dedup checks.
- Implement `SummaryInputBuilder` to group X by handle and Reddit by subreddit/post/comment.
- Add `stock-sum payload build`.
- Add unit and CLI tests.
- Generate `docs/examples/summary_input_sample.json`.
- Run pytest, config validation, and git diff checks.
