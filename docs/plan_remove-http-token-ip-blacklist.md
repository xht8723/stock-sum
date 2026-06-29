# Remove HTTP Token Auth And Add IP Blacklist

## Goals
- Remove bearer-token authentication from the local `stock-sum` HTTP API.
- Allow any non-blacklisted client to call the API.
- Add a configurable exact-IP blacklist that rejects matching clients with HTTP 403.
- Remove token handling from the Redbot cog.

## Checklist
- Replace the `/v1/*` bearer-token dependency with an IP blacklist dependency.
- Change server config from `auth_token_env` to `blacklisted_ips`.
- Remove `STOCK_SUM_HTTP_TOKEN` from env examples, docs, and Redbot cog docs.
- Update Redbot HTTP client to stop sending `Authorization`.
- Update API and Redbot tests for open access and blacklist behavior.
- Run pytest, config validation, and `git diff --check`.
