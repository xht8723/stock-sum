# stock-sum

`stock-sum` is a Python 3.12 service for collecting trading-related information,
summarizing it through LLM providers, rendering reports, and delivering those
reports through channels such as email or WhatsApp.

The project is currently an API-first service scaffold:

- FastAPI daemon and health/config/report-trigger routes.
- Typer CLI for configuration, profile management, daemon startup, and manual
  report runs.
- TOML-based configuration with Pydantic validation.
- Cache-first `models.dev` metadata refresh support.
- Xpoz API collectors for X user timelines and Reddit subreddit posts.
- Generic collector interfaces and Playwright infrastructure for future
  site-specific collectors.
- SQLite shared collection run/index storage plus source-specific X/Reddit raw
  tables.

The bundled X and Reddit collectors are disabled in the example config by
default. Enable the collector you want, set `XPOZ_API_KEY` in the process
environment, and reference the collector from a report profile.

## Requirements

- Python 3.12 or newer.
- pip and venv.
- Chromium browser dependencies for future Playwright collectors.
- Docker and Docker Compose, if deploying with containers.
- API credentials supplied through environment variables when integrations are
  enabled.

Secrets must stay in environment variables. The TOML files store the names of
secret environment variables, not the secret values.

## Project layout

```text
stock_sum/
  api/                 FastAPI app factory and routes
  collectors/          Collector interfaces and future collector namespaces
  config/              Pydantic models, TOML loader, defaults, examples
  core/                Shared pipeline models, errors, and context
  delivery/            Delivery provider scaffolding
  llm/                 LLM provider interfaces and models.dev catalog cache
  reports/             Report renderer scaffolding
  scheduler/           Scheduler job scaffolding
  service/             Daemon assembly
  storage/             SQLite shared run/index persistence
docs/
  deployment.md
```

## Quick start

Create and activate a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
python -m playwright install chromium
```

On Linux hosts, install Chromium system dependencies as well:

```bash
python -m playwright install --with-deps chromium
```

Create a local config and env file with the first-run wizard.

```powershell
stock-sum setup init --config config.toml --env-file .env
stock-sum setup check --config config.toml --env-file .env
```

Start the HTTP daemon.

```powershell
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
```

Check the service.

```powershell
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/effective
```

## Configuration

The application reads TOML configuration files. A readable starter config is
available at `stock_sum/config/example.toml`, and the built-in defaults are in
`stock_sum/config/defaults.toml`.

Create a local config file:

```powershell
stock-sum config init config.toml
stock-sum config validate config.toml
```

Read and update individual values:

```powershell
stock-sum config get config.toml service.timezone
stock-sum config set config.toml service.timezone "'America/Vancouver'"
stock-sum config set config.toml playwright.channel "'chromium'"
```

Manage report profiles:

```powershell
stock-sum config profile list --config config.toml
stock-sum config profile show default --config config.toml
stock-sum config profile add closing --config config.toml --timezone America/Vancouver --schedule "0 16 * * 1-5" --collectors api.market_watch --deliveries email.primary
stock-sum config profile edit closing --config config.toml --collectors api.market_watch,api.news
stock-sum config profile delete closing --config config.toml
```

Manage long source lists:

```powershell
stock-sum config x-user list --config config.toml
stock-sum config x-user add aleabitoreddit --config config.toml --profile default --limit 10
stock-sum config x-user delete aleabitoreddit --config config.toml --profile default
stock-sum config subreddit list --config config.toml
stock-sum config subreddit add wallstreetbets --config config.toml --profile default --sort new --limit 10
stock-sum config subreddit delete wallstreetbets --config config.toml --profile default
```

Important configuration sections:

- `[service]`: service name and default timezone.
- `[server]`: local HTTP host, port, artifact directory, one-hour report cache,
  and exact IP blacklist.
- `[storage]`: SQLite database path.
- `[retention]`: managed runtime data cap and cleanup behavior. Defaults to a
  2GB total cap across HTTP job artifacts, downloaded media, and SQLite data.
- `[models_dev]`: external model catalog URL, cache path, and refresh interval.
- `[playwright]`: browser automation defaults for future site-specific browser
  collectors.
- `[providers.xpoz]`: Xpoz MCP-over-HTTP server URL, timeout, and API key
  environment-variable name for X and Reddit collection.
- `[llm]`: DeepSeek provider settings, model, timeout, temperature, token cap,
  and API key environment-variable name.
- `[reports.*]`: named report profiles and cron schedules.
- `[[sources.x_users]]`: long-list X user sources. Each one resolves to
  collector ID `x.<handle>`.
- `[[sources.subreddits]]`: long-list subreddit sources. Each one resolves to
  collector ID `reddit.<subreddit>`.
- `[collectors.*.*]`: generic future collector definitions.
- `[delivery.email.*]` and `[delivery.whatsapp.*]`: delivery definitions.

Use `.env.example` as the template for local environment variables:

```powershell
Copy-Item .env.example .env
```

The easier path is to let the CLI write secret values without printing them:

```powershell
stock-sum secrets set XPOZ_API_KEY --env-file .env
stock-sum secrets set DEEPSEEK_API_KEY --env-file .env
stock-sum secrets list --env-file .env
```

The first-run wizard does this for required keys:

```powershell
stock-sum setup init --config config.toml --env-file .env
stock-sum setup check --config config.toml --env-file .env
```

For a one-off PowerShell session, set API keys directly:

```powershell
$env:XPOZ_API_KEY = "your_real_key"
$env:DEEPSEEK_API_KEY = "your_real_key"
```

The TOML config stores only the variable name:

```toml
[providers.xpoz]
api_key_env = "XPOZ_API_KEY"
server_url = "https://mcp.xpoz.ai/mcp"
timeout_seconds = 60
```

## CLI

Show top-level help:

```powershell
stock-sum --help
```

Configuration commands:

```powershell
stock-sum setup init --config config.toml --env-file .env
stock-sum setup check --config config.toml --env-file .env
stock-sum setup reset --config config.toml --env-file .env --data-dir data
stock-sum secrets set XPOZ_API_KEY --env-file .env
stock-sum secrets list --env-file .env
stock-sum llm providers
stock-sum config init config.toml
stock-sum config validate config.toml
stock-sum config get config.toml llm.model
stock-sum config set config.toml llm.model "'deepseek-v4-flash'"
stock-sum config sync --config config.toml
stock-sum config sync --config config.toml --force
stock-sum config profile list --config config.toml
stock-sum config profile add closing --config config.toml --collectors api.market_watch --deliveries email.primary
stock-sum config x-user add aleabitoreddit --config config.toml --profile default
stock-sum config subreddit add wallstreetbets --config config.toml --profile default
```

Daemon and report commands:

```powershell
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
stock-sum collect --profile default --config config.toml
stock-sum collect --collector x.aleabitoreddit --config config.toml
stock-sum collect --collector reddit.wallstreetbets --config config.toml
stock-sum payload build --profile default --output docs/examples/summary_input_sample.json --config config.toml --download-images --mode vision
stock-sum llm summarize --profile default --payload docs/examples/summary_input_sample.json --output C:\tmp\stock-sum-deepseek-response.json --config config.toml
stock-sum report render --input C:\tmp\stock-sum-deepseek-response.json --mode html --output C:\tmp\stock-sum-report.html
stock-sum report render --input C:\tmp\stock-sum-deepseek-response.json --mode markdown --output C:\tmp\stock-sum-report.md
stock-sum report render --input C:\tmp\stock-sum-deepseek-response.json --mode text --output C:\tmp\stock-sum-report.txt
stock-sum run-report --profile default --config config.toml
```

`setup reset` is destructive. It prints the target config, env file, and data
directory, then requires confirmation and typing `RESET` before deletion.

The bundled example sources are disabled. To use them, either set
`enabled = true` in TOML or add a source through the CLI, then run the generated
collector ID directly or through a report profile. X and Reddit collection use
Xpoz through an internal MCP-over-HTTP client.

`payload build` reads collected data from SQLite and writes an LLM-ready JSON
payload with separate X and Reddit sections. X posts are grouped by handle;
Reddit posts are grouped by subreddit with comments nested under the matching
post. With `--download-images`, eligible image media is saved under ignored
`data/media/` and referenced from the JSON payload. Use `--mode full` for a
debug payload, `--mode compact` for a lower-token text payload, and
`--mode vision` for the compact payload plus an ordered image attachment
manifest.

`llm summarize` sends the compact payload text to the configured DeepSeek model
and writes the full response metadata plus parsed JSON summary to the requested
output file. The first provider client is text-only; media is referenced by
media IDs, URLs, or local paths rather than uploaded as image bytes.

`report render` turns the LLM response JSON into final presentation artifacts.
Use `--mode html` for a standalone visual report, `--mode markdown` for a
portable document, `--mode discord` for Discord-friendly markdown, or
`--mode text` for plain email/terminal output.

## HTTP API

Start the daemon, then use these endpoints. The local API is open to any
non-blacklisted client; configure exact IP blocks with `[server].blacklisted_ips`.
Successful report jobs are cached for `[server].report_cache_ttl_seconds`
seconds, defaulting to one hour. Set it to `0` to disable report reuse.

- `GET /health`: returns service health.
- `GET /v1/config/effective`: returns the loaded configuration. Secret values are
  not stored in config; only environment variable names are present.
- `POST /v1/reports/{profile}/jobs`: starts a full async report job using a
  body `mode` field.
- `POST /v1/reports/{profile}/jobs/{mode}`: starts a full async report job for
  a specific output mode. Supported modes are `html`, `markdown`, `discord`,
  `text`, and `json`.
- `GET /v1/jobs/{job_id}`: checks job status.
- `GET /v1/jobs/{job_id}/artifact`: downloads the rendered report artifact.

Example:

```powershell
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/v1/reports/default/jobs/html `
  -H "Content-Type: application/json" `
  -d '{"include_capitol_trades":true}'
```

The response includes a `job_id` that can be polled until the report succeeds or
fails.

## Runtime Data Retention

`stock-sum` prunes managed runtime data after report and collection pipeline
runs when `[retention].enabled` and `[retention].prune_after_pipeline` are true.
The default cap is `2147483648` bytes across `[server].artifact_dir`,
`[media].root_dir`, and `[storage].sqlite_path`.

```powershell
stock-sum retention status --config config.toml
stock-sum retention prune --dry-run --config config.toml
stock-sum retention prune --apply --config config.toml
```

Cleanup deletes oldest HTTP job artifacts first, downloaded media next, and old
SQLite collection history last. The current HTTP job artifact is protected while
cleanup runs so API clients can still download it.

## Docker quick start

Build and start the daemon with Docker Compose:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The compose file:

- Builds the local `Dockerfile`.
- Exposes the daemon on `http://127.0.0.1:8000`.
- Mounts `./data` to `/app/data` for SQLite files, cache files, and future
  browser profiles.
- Loads `.env` if present.

For detailed bare-machine and Docker deployment instructions, see
`docs/deployment.md`.

## Development

Install development dependencies:

```powershell
pip install -e ".[dev]"
```

Run tests:

```powershell
pytest
```

Run CLI smoke checks:

```powershell
stock-sum --help
stock-sum collect --help
```

## Current limitations

- Xpoz X/Reddit collectors are implemented, but disabled by default.
- The first implementation persists remote media URLs and metadata; image
  downloads are available through `payload build --download-images`.
- Each future API integration should get its own source-specific raw tables.
- Scheduler jobs are configured in memory but scheduler execution is scaffolded.
- LLM providers, report rendering, email delivery, and WhatsApp delivery are
  protocol/provider skeletons.
