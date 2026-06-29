# Discord Format Endpoints Refactor Plan

## Goal
Make stock-sum expose report jobs through format-specific HTTP endpoints, then update the Redbot cog so `/report` defaults to a Discord-specific markdown artifact that can be sent inline. Non-Discord formats remain file attachments.

## Changes
- Keep `POST /v1/reports/{profile}/jobs` for compatibility.
- Add `POST /v1/reports/{profile}/jobs/{mode}` for `html`, `markdown`, `discord`, `text`, and `json`.
- Add a `discord` presentation render mode optimized for Discord markdown.
- Update the Redbot cog to call the format-specific endpoint and default to `discord`.
- Send Discord markdown inline when it fits Discord message limits; otherwise attach it as a file.
- Keep HTML, Markdown, Text, and JSON as file attachments.
- Update tests and docs for the new endpoint and default behavior.
