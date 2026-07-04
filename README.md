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
- `[llm]`: LLM provider/model/runtime settings.
- `[reports.*]`: named manual report profiles with `collector_ids`.
- `[sources.*]`: X users, subreddits, House PTR, and SEC 13F source settings.
- `[collectors.*]`: optional explicit collector blocks.

The default config is tuned for a 1GB VM: collector, Xpoz, LLM analysis, and
House PTR PDF parsing concurrency all default to `1`; image downloading is off;
and generated artifacts are capped by retention.

Profile management:

```powershell
.\.venv\Scripts\stock-sum.exe config profile add closing --collectors x.aleabitoreddit,reddit.wallstreetbets
.\.venv\Scripts\stock-sum.exe config profile edit closing --collectors x.aleabitoreddit,reddit.wallstreetbets,house.ptr
.\.venv\Scripts\stock-sum.exe config profile show closing
```

Source management:

```powershell
.\.venv\Scripts\stock-sum.exe config x-user add aleabitoreddit --profile default
.\.venv\Scripts\stock-sum.exe config subreddit add wallstreetbets --profile default
.\.venv\Scripts\stock-sum.exe config house-ptr set --profile default
```

## CLI Workflows

Heavy CLI commands such as `collect`, `run-report`, `llm summarize`, and
`llm analyze` run their work in a short-lived child Python process. The parent
CLI process stays small and mirrors the child output.

Collect configured source data:

```powershell
.\.venv\Scripts\stock-sum.exe collect --profile default
```

Build an LLM payload from SQLite:

```powershell
.\.venv\Scripts\stock-sum.exe payload build --profile default --output payload.json
```

Run LLM analysis:

```powershell
.\.venv\Scripts\stock-sum.exe llm analyze --profile default --output response.json
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

- `POST /v1/social-reports/{profile}/jobs`
- `POST /v1/social-reports/{profile}/jobs/{mode}`
- `POST /v1/trading-reports/jobs`
- `POST /v1/trading-reports/jobs/{mode}`
- `POST /v1/13f-reports/jobs`
- `POST /v1/13f-reports/jobs/{mode}`
- `POST /v1/collect/{profile}/jobs`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/artifact`

Supported modes are `html`, `markdown`, `discord`, `text`, and `json`.

HTTP jobs are coordinated by the daemon but executed by one short-lived child
worker process per job. The job status payload includes worker diagnostics such
as `worker_pid`, `worker_exit_code`, and `worker_runtime_seconds`; stale
`queued` or `running` jobs from a daemon restart are marked failed on startup.

Example:

```powershell
$job = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:8000/v1/social-reports/default/jobs/discord `
  -ContentType 'application/json' `
  -Body '{"detail":"minimum"}'

Invoke-RestMethod http://127.0.0.1:8000/v1/jobs/$($job.job_id)
```

Management endpoints under `/v1/profiles`, `/v1/sources`, `/v1/llm`,
`/v1/secrets`, `/v1/setup`, and `/v1/retention` are local-only by default.
Set `[server].management_allow_remote = true` only when the service is protected
by a trusted network boundary.

## Redbot Cog

The Redbot cog in `redbot_cogs/stocksum_report` calls the canonical HTTP job
API and exposes:

- `/socialreport`
- `/tradingreport`
- `/13freport`
- `/stocksum ...` management commands

The default report format is Discord-specific markdown sent inline in chunks.
Other formats are returned as Discord file attachments.

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
