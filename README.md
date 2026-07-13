# stock-sum

`stock-sum` is a local API-first service for collecting market-adjacent source
data, storing it in SQLite, asking an LLM for analysis, and rendering report
artifacts for automation clients such as the bundled Redbot cog.

Implemented source paths are Xpoz-backed X/Reddit collection, House PTR
disclosures, and SEC 13F datasets. Report delivery is handled by clients that
call the HTTP API and download artifacts; the service itself does not send
email, WhatsApp, or scheduled outbound messages.

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
.\.venv\Scripts\stock-sum.exe setup init --config config.toml --env-file .env --overwrite
```

Set these secrets in `.env` or through `stock-sum secrets`:

- `XPOZ_API_KEY`
- `DEEPSEEK_API_KEY`

Optional:

- `ADANOS_API_KEY` enables `/trendings` and the matching HTTP endpoint. If it
  is missing, trendings requests return a short empty-state report without
  treating the missing key as a warning.

After `setup init`, stock-sum remembers the active config and env file paths in
`.stock-sum-state.json`. Related CLI commands use those remembered paths by
default; explicit `--config` and `--env-file` flags still override them.

Run a setup check:

```powershell
.\.venv\Scripts\stock-sum.exe setup check
```

## Configuration

The packaged example config is at `stock_sum/config/example.toml`.

Important sections:

- `[service]`: process name and collector concurrency.
- `[server]`: local HTTP host, port, artifact directory, report cache, job
  retention, in-flight coalescing, and bounded in-memory job status.
- `[storage]`: SQLite path.
- `[media]`: optional image download limits.
- `[retention]`: generated artifact/media cleanup behavior.
- `[models_dev]`: cache-backed model catalog metadata.
- `[providers.xpoz]`: Xpoz MCP-over-HTTP provider settings.
- `[providers.adanos]`: Adanos Reddit/X trendings provider settings.
- `[llm]`: LLM provider/model/runtime settings.
- `[sources.*]`: X users, subreddits, House PTR, and SEC 13F source settings.
- `[collectors.*]`: optional explicit collector blocks.

The default config is tuned for a 1GB VM: collector, Xpoz, LLM analysis, and
House PTR PDF parsing concurrency all default to `1`; image downloading is off;
and generated artifacts are capped by retention.

Source management:

```powershell
.\.venv\Scripts\stock-sum.exe config x-user add aleabitoreddit
.\.venv\Scripts\stock-sum.exe config subreddit add wallstreetbets
.\.venv\Scripts\stock-sum.exe config house-ptr set
```

## CLI Workflows

Heavy CLI commands such as `collect`, `llm summarize`, and `llm analyze` run
their work in a short-lived child Python process. The parent
CLI process stays small and mirrors the child output.

Collect configured source data:

```powershell
.\.venv\Scripts\stock-sum.exe collect
```

Build an LLM payload from SQLite:

```powershell
.\.venv\Scripts\stock-sum.exe payload build --output payload.json
```

Run LLM analysis:

```powershell
.\.venv\Scripts\stock-sum.exe llm analyze --output response.json
```

Render a stored LLM response:

```powershell
.\.venv\Scripts\stock-sum.exe report render response.json --mode discord --output report.md
```

## HTTP API

Start the service:

```powershell
.\.venv\Scripts\stock-sum.exe daemon --host 127.0.0.1 --port 8000
```

Canonical job endpoints:

- `POST /v1/social-reports/jobs`
- `POST /v1/social-reports/jobs/{mode}`
- `POST /v1/trading-reports/jobs`
- `POST /v1/trading-reports/jobs/{mode}`
- `POST /v1/13f-reports/jobs`
- `POST /v1/13f-reports/jobs/{mode}`
- `POST /v1/trendings/jobs`
- `POST /v1/trendings/jobs/{mode}`
- `POST /v1/statistics/jobs`
- `GET /v1/statistics/fuzzy-matches`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/artifact`
- `GET /v1/sources`
- `GET|POST|DELETE /v1/sources/x-users`
- `GET|POST|DELETE /v1/sources/subreddits`

Supported modes are `html`, `markdown`, `discord`, `text`, and `json`.
Statistics jobs are separate from report modes: they render a PNG chart plus a
JSON summary sidecar from existing SQLite social analysis or House PTR rows.
Statistic fuzzy matching can resolve Discord `fuzzy_search` input from social
analysis tags or House PTR asset names before a PNG job is created.
Trendings jobs query Adanos Reddit Stocks and X Stocks stock/sector trend
endpoints with the provider maximum fetch limit, store raw and normalized rows
in SQLite, and render the requested output mode using the display limit only.
When dates are omitted, trendings reports default to the latest 24-hour UTC
window; notable change comparison still looks back 7 days by default.

HTTP jobs are coordinated by the daemon but executed by one short-lived child
worker process per job. The job status payload includes worker diagnostics such
as `worker_pid`, `worker_exit_code`, and `worker_runtime_seconds`; stale
`queued` or `running` jobs from a daemon restart are marked failed on startup.

Example:

```powershell
$job = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/social-reports/jobs/discord `
  -ContentType 'application/json' `
  -Body '{"detail":"minimum"}'

Invoke-RestMethod http://127.0.0.1:8000/v1/jobs/$($job.job_id)
```

The X/Reddit source settings endpoints are local-only by default. Set
`[server].management_allow_remote = true` only when the service is protected by
a trusted network boundary.

## Redbot Cog

The Redbot cog in `redbot_cogs/stocksum_report` calls the canonical HTTP job
API and exposes:

- `/recent_posts`
- `/ptr_search`
- `/13f_search`
- `/trendings`
- `/plot`
- `/settings ...` source settings commands

Report commands always use Discord-specific markdown sent inline in chunks.
`/trendings` returns recent Adanos stock and sector trend rows from Reddit and
X. The slash command always uses the latest 24-hour UTC window; `limit` controls
trend rows and comparison tickers, and optional `comparison_days` controls
notable-change history.
`/plot` returns a PNG attachment for social sentiment or House disclosure
activity over time. Public report/plot slash commands no longer expose a
private-mode option. `/settings list` is public; owner-only source mutations
respond privately.

## Runtime Data

Generated runtime data is ignored by git:

- SQLite databases under `data/` or custom configured paths.
- HTTP job artifacts under `[server].artifact_dir`.
- Downloaded media under `[media].root_dir`.
- Local cache/build/test artifacts such as `.pytest_cache/`, `temp/`, and
  `stock_sum.egg-info/`.

Inspect or prune managed runtime data:

```powershell
.\.venv\Scripts\stock-sum.exe retention status --config config.toml
.\.venv\Scripts\stock-sum.exe retention prune --dry-run --config config.toml
.\.venv\Scripts\stock-sum.exe retention prune --apply --config config.toml
```

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
docker compose config
```
