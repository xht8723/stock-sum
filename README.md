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
- Scrape Creators API collectors for X user tweets and Reddit subreddit posts.
- Generic collector interfaces and Playwright infrastructure for future
  site-specific collectors.
- SQLite shared collection run/index storage plus source-specific Scrape
  Creators X/Reddit raw tables.

The bundled X and Reddit collectors are disabled in the example config by
default. Enable the collector you want, set `SCRAPE_CREATORS_API_KEY` in the
process environment, and reference the collector from a report profile.

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

Validate the bundled example configuration.

```powershell
stock-sum config validate stock_sum/config/example.toml
```

Start the HTTP daemon.

```powershell
stock-sum daemon --config stock_sum/config/example.toml --host 127.0.0.1 --port 8000
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
- `[storage]`: SQLite database path.
- `[models_dev]`: external model catalog URL, cache path, and refresh interval.
- `[playwright]`: generic browser automation defaults for future collectors.
- `[providers.scrape_creators]`: Scrape Creators API base URL, timeout, and API
  key environment-variable name.
- `[llm]`: provider, model, and API key environment variable name.
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

Then fill in values such as `SCRAPE_CREATORS_API_KEY`, `OPENAI_API_KEY`,
`SMTP_USERNAME`, `SMTP_PASSWORD`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_WHATSAPP_FROM` as needed.

For a one-off PowerShell session, set the Scrape Creators key directly:

```powershell
$env:SCRAPE_CREATORS_API_KEY = "your_real_key"
```

The TOML config stores only the variable name:

```toml
[providers.scrape_creators]
api_key_env = "SCRAPE_CREATORS_API_KEY"
base_url = "https://api.scrapecreators.com"
timeout_seconds = 30
```

## CLI

Show top-level help:

```powershell
stock-sum --help
```

Configuration commands:

```powershell
stock-sum config init config.toml
stock-sum config validate config.toml
stock-sum config get config.toml llm.model
stock-sum config set config.toml llm.model "'gpt-4.1-mini'"
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
stock-sum run-report --profile default --config config.toml
```

The bundled example sources are disabled. To use them, either set
`enabled = true` in TOML or add a source through the CLI, then run the generated
collector ID directly or through a report profile. Scrape Creators' X
user-tweets endpoint may return popular public tweets rather than a strict
latest-only timeline, so treat it as provider-ranked public tweet data.

## HTTP API

Start the daemon, then use these endpoints:

- `GET /health`: returns service health.
- `GET /config/effective`: returns the loaded configuration. Secret values are
  not stored in config; only environment variable names are present.
- `POST /reports/{profile}/run`: accepts a manual report trigger if the profile
  exists in the loaded config.

Example:

```powershell
curl http://127.0.0.1:8000/health
curl -X POST http://127.0.0.1:8000/reports/default/run
```

The report trigger currently returns an accepted response from the API layer; it
does not execute a completed summary/delivery pipeline yet.

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

- Scrape Creators X/Reddit collectors are implemented, but disabled by default.
- The first implementation persists remote media URLs and metadata only; it does
  not download image files.
- Each future API integration should get its own source-specific raw tables.
- Scheduler jobs are configured in memory but scheduler execution is scaffolded.
- LLM providers, report rendering, email delivery, and WhatsApp delivery are
  protocol/provider skeletons.
