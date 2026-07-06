# Discord Slash Required Parameters

## Goals
- Make Discord-native required slash options explicit for fields that commands cannot run without.
- Keep conditional filter groups optional and validated at runtime.
- Improve slash-command descriptions so users know which optional filters are conditionally required.
- Avoid HTTP/backend behavior changes.

## Checklist
- Audit Redbot slash command callback signatures.
- Remove defaults only from direct identifiers that should be Discord-required.
- Keep report/statistic filter groups optional with existing validation.
- Tighten descriptions for conditional filter commands.
- Add tests for required/optional callback signatures and conditional validation.
- Run pytest, config validation, and `git diff --check`.
