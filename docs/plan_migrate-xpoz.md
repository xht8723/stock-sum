# Migrate Collectors From Scrape Creators To Xpoz

## Goals
- Replace Scrape Creators with Xpoz for X and Reddit collection.
- Keep existing user-facing collector IDs and report payload shape.
- Remove Scrape Creators config, code, tests, and docs references.
- Rename Reddit storage tables to provider-neutral names.
- Preserve generic Playwright infrastructure for future non-X/non-Reddit scraping.

## Checklist
1. Add Xpoz provider config and a reusable HTTP MCP client.
2. Implement Xpoz X and Reddit collectors.
3. Wire collector factory source lists to Xpoz collector kinds.
4. Remove Scrape Creators implementation and config references.
5. Rename Reddit storage tables and mappers to provider-neutral table names.
6. Update README, deployment docs, tests, and example/default TOML.
7. Run tests, config validation, diff checks, database reset, live collects, payload build, and HTML render.
