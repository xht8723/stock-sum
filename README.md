# stock-sum

`stock-sum` is a Python 3.12 service for collecting trading-related information,
summarizing it through LLM providers, rendering reports, and delivering those
reports through channels such as email or WhatsApp.

The project is currently a service scaffold with one real collector path in
progress:

- FastAPI daemon and health/config/report-trigger routes.
- Typer CLI for configuration, daemon startup, manual report runs, and X.com
  scraping utilities.
- TOML-based configuration with Pydantic validation.
- Cache-first `models.dev` metadata refresh support.
- A Playwright-based X.com user timeline collector with persistent browser
  profile support.
- Protocol and storage/report/delivery/scheduler scaffolding for later business
  logic.

The full report pipeline, scheduler execution, LLM provider calls, report
rendering, and delivery providers are intentionally scaffolded only. Calling
`stock-sum run-report` currently validates configuration and then raises the
scaffolded pipeline error.

## Requirements

- Python 3.12 or newer.
- pip and venv.
- Chromium browser dependencies for Playwright.
- Docker and Docker Compose, if deploying with containers.
- API credentials supplied through environment variables when integrations are
  enabled.

Secrets must stay in environment variables. The TOML files store the names of
secret environment variables, not the secret values.

## Project layout

```text
stock_sum/
  api/                 FastAPI app factory and routes
  collectors/          Collector interfaces and Playwright collectors
  config/              Pydantic models, TOML loader, defaults, examples
  core/                Shared pipeline models, errors, and context
  delivery/            Delivery provider scaffolding
  llm/                 LLM provider interfaces and models.dev catalog cache
  reports/             Report renderer scaffolding
  scheduler/           Scheduler job scaffolding
  service/             Daemon assembly
  storage/             SQLAlchemy storage scaffolding
docs/
  plan_initial-architecture-setup.md
  plan_x-playwright-collector.md
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
stock-sum config set config.toml playwright.x.max_posts 20
```

Important configuration sections:

- `[service]`: service name and default timezone.
- `[storage]`: SQLite database path.
- `[models_dev]`: external model catalog URL, cache path, and refresh interval.
- `[playwright]`: browser automation defaults.
- `[playwright.x]`: X.com profile path, scraping limits, and login behavior.
- `[llm]`: provider, model, and API key environment variable name.
- `[reports.*]`: named report profiles and cron schedules.
- `[collectors.*.*]`: collector definitions referenced by report profiles.
- `[delivery.email.*]` and `[delivery.whatsapp.*]`: delivery definitions.

Use `.env.example` as the template for local environment variables:

```powershell
Copy-Item .env.example .env
```

Then fill in values such as `OPENAI_API_KEY`, `SMTP_USERNAME`,
`SMTP_PASSWORD`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and
`TWILIO_WHATSAPP_FROM` as needed.

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
```

Daemon and report commands:

```powershell
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
stock-sum run-report --profile morning --config config.toml
```

`run-report` is not production-ready yet because the full report pipeline is
still scaffolded.

X.com collector commands:

```powershell
stock-sum x status --config config.toml
stock-sum x login --config config.toml --channel chrome --wait-seconds 600
stock-sum x scrape --config config.toml --handle aleabitoreddit --limit 10
```

The login command opens a headed browser and stores the session under the
configured `playwright.x.user_data_dir`. Later scrape commands reuse that
persistent profile in headless mode.

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
curl -X POST http://127.0.0.1:8000/reports/morning/run
```

The report trigger currently returns an accepted response from the API layer; it
does not execute a completed pipeline yet.

## Docker quick start

Build and start the daemon with Docker Compose:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

The compose file:

- Builds the local `Dockerfile`.
- Exposes the daemon on `http://127.0.0.1:8000`.
- Mounts `./data` to `/app/data` for SQLite, cache files, and browser profiles.
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

Run a CLI smoke check:

```powershell
stock-sum --help
stock-sum x --help
```

## Current limitations

- The end-to-end report pipeline is intentionally not implemented yet.
- Scheduler jobs are configured in memory but scheduler execution is scaffolded.
- LLM providers, report rendering, storage persistence, email delivery, and
  WhatsApp delivery are protocol/provider skeletons.
- X.com scraping depends on X page structure, rate limits, and login state.
- Dockerized headed login is not configured by default; create or refresh the X
  browser profile on a machine with a visible browser, then persist/mount the
  profile data.
