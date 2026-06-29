# Discord Report Reply Improvements Plan

## Goals
- Acknowledge `/report` immediately instead of leaving Discord in a waiting state.
- Send successful Discord-format reports as clean report content only, split into multiple messages when needed.
- Keep non-Discord formats as file attachments and keep failures explicit.

## Checklist
- Replace deferred response with an immediate acknowledgement message.
- Add Discord markdown chunking that prefers blank-line and line boundaries under a safe message limit.
- Remove job/profile/format/warning boilerplate from successful replies.
- Send non-Discord artifacts as file attachments with minimal required message content.
- Add unit tests for chunking and command-level Redbot behavior.
