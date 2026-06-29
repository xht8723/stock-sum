# Compact And Vision Payload Modes

## Goals
- Add compact LLM payload output that avoids repeated media metadata and null fields.
- Add vision payload output that includes an ordered image attachment manifest.
- Keep the full payload output available for debugging.
- Generate a real compact/vision payload file for examination.

## Checklist
- Add `full`, `compact`, and `vision` serialization modes to `SummaryInput`.
- Add shared media ID map, per-post media references, and image selection caps.
- Drop null and empty fields from compact/vision payloads.
- Add CLI options for payload mode and media caps.
- Add tests for compact and vision output.
- Run tests, validation, diff checks, and a live payload build.
