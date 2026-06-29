# Redbot `/report` Cog Plan

## Goals
- Add a Red Discord Bot cog in a new folder that exposes `/report`.
- Keep the cog thin: call the local `stock-sum` HTTP API, poll the async job, download the final artifact, and send it back to Discord.
- Keep all secrets in environment variables.

## Checklist
- Create `redbot_cogs/stocksum_report/` with Red cog package files.
- Implement `/report` options for profile, format, Capitol Trades inclusion, and private/public response.
- Add a reusable async HTTP client that calls `POST /v1/reports/{profile}/jobs`, polls `GET /v1/jobs/{job_id}`, and downloads `GET /v1/jobs/{job_id}/artifact`.
- Use `STOCK_SUM_BASE_URL` with default `http://127.0.0.1:8000` and required `STOCK_SUM_HTTP_TOKEN`.
- Convert stock-sum HTTP failures, offline service errors, job failure, and timeout into clear Discord messages.
- Add tests using a fake HTTP session so no Redbot, Discord, or live stock-sum process is required.
- Run pytest and config validation.
