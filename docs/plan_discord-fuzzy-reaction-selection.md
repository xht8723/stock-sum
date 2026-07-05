# Fix Discord Fuzzy Reaction Selection

## Goals
- Make `/statistic fuzzy_search` selectable by clicking bot-added reactions.
- Accept Discord's alternate numeric reaction forms such as `1` and `1️⃣`.
- Keep selection public and scoped to the requesting user and the candidate message.
- Avoid crashing if the bot cannot add reactions.

## Checklist
- Add a reaction normalization helper for choices 1 through 5.
- Use normalized reactions for selection checks and selected index lookup.
- Keep pre-adding bot reactions, but tolerate reaction-add failures.
- Clarify the fuzzy selection prompt text.
- Extend Redbot tests for pre-added reactions, plain numeric reactions, ignored users, unsupported emoji timeout, and add-reaction failures.
- Run pytest, config validation, and `git diff --check`.
