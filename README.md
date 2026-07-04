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

Run a setup check:

```powershell
.\.venv\Scripts\stock-sum.exe setup check --config config.toml --env-file .env
```

## Configuration

The packaged example config is at `stock_sum/config/example.toml`.

Important sections:

- `[service]`: process name and collector concurrency.
- `[server]`: local HTTP host, port, artifact directory, report cache, job
  retention, and in-flight coalescing.
- `[storage]`: SQLite path.
- `[media]`: optional image download limits.
- `[retention]`: generated artifact/media cleanup behavior.
- `[models_dev]`: cache-backed model catalog metadata.
- `[providers.xpoz]`: Xpoz MCP-over-HTTP provider settings.
- `[llm]`: LLM provider/model/runtime settings.
- `[reports.*]`: named manual report profiles with `collector_ids`.
- `[sources.*]`: X users, subreddits, House PTR, and SEC 13F source settings.
- `[collectors.*]`: optional explicit collector blocks.

Profile management:

```powershell
.\.venv\Scripts\stock-sum.exe config profile add closing --config config.toml --collectors x.aleabitoreddit,reddit.wallstreetbets
.\.venv\Scripts\stock-sum.exe config profile edit closing --config config.toml --collectors x.aleabitoreddit,reddit.wallstreetbets,house.ptr
.\.venv\Scripts\stock-sum.exe config profile show closing --config config.toml
```

Source management:

```powershell
.\.venv\Scripts\stock-sum.exe config x-user add aleabitoreddit --config config.toml --profile default
.\.venv\Scripts\stock-sum.exe config subreddit add wallstreetbets --config config.toml --profile default
.\.venv\Scripts\stock-sum.exe config house-ptr set --config config.toml --profile default
```

## CLI Workflows

Collect configured source data:

```powershell
.\.venv\Scripts\stock-sum.exe collect --profile default --config config.toml
```

Build an LLM payload from SQLite:

```powershell
.\.venv\Scripts\stock-sum.exe payload build --profile default --config config.toml --output payload.json
```

Run LLM analysis:

```powershell
.\.venv\Scripts\stock-sum.exe llm analyze --profile default --config config.toml --output response.json
```

Render a stored LLM response:

```powershell
.\.venv\Scripts\stock-sum.exe report render response.json --mode discord --output report.md
```

## HTTP API

Start the service:

```powershell
.\.venv\Scripts\stock-sum.exe daemon --config config.toml --env-file .env --host 127.0.0.1 --port 8000
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
