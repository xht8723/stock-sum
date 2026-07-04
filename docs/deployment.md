# Deployment

This guide deploys `stock-sum` as a local or VM-hosted HTTP service. The service
collects data through Xpoz, official disclosure sources, SQLite, DeepSeek, and
HTTP job artifacts. It does not run scheduled outbound delivery; automation
clients such as Redbot request reports through the API.

## Bare Machine

Create a service account and install Python 3.12:

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin stocksum
sudo mkdir -p /opt/stock-sum/app
sudo chown -R stocksum:stocksum /opt/stock-sum
```

Install the app:

```bash
cd /opt/stock-sum/app
python3.12 -m venv .venv
. .venv/bin/activate
pip install .
```

Create config and secrets:

```bash
stock-sum setup init --config config.toml --env-file .env --overwrite
stock-sum secrets set XPOZ_API_KEY
stock-sum secrets set DEEPSEEK_API_KEY
stock-sum setup check
```

After `setup init`, stock-sum remembers the active config and env file paths in
`.stock-sum-state.json`. Most CLI commands use those remembered paths by
default. Pass `--config` or `--env-file` only when intentionally overriding the
active setup.

Run locally:

```bash
stock-sum daemon --host 127.0.0.1 --port 8000
```

Expose to trusted clients by binding to `0.0.0.0` only behind an appropriate
firewall or reverse proxy.

## systemd

Example unit:

```ini
[Unit]
Description=stock-sum HTTP API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=stocksum
Group=stocksum
WorkingDirectory=/opt/stock-sum/app
EnvironmentFile=/opt/stock-sum/app/.env
ExecStart=/opt/stock-sum/app/.venv/bin/stock-sum daemon --config /opt/stock-sum/app/config.toml --env-file /opt/stock-sum/app/.env --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and inspect:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-sum
sudo systemctl status stock-sum
journalctl -u stock-sum -f
```

## Docker

Build and run:

```bash
docker compose up --build
```

The compose file maps `./data` to `/app/data` and optionally reads `.env`.
For production, mount a config file and pass it through the command:

```yaml
services:
  stock-sum:
    build: .
    command: ["stock-sum", "daemon", "--config", "/app/config.toml", "--env-file", "/app/.env", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "8000:8000"
    volumes:
      - ./config.toml:/app/config.toml:ro
      - ./data:/app/data
    env_file:
      - .env
```

## Redbot

Install the cog path on the same VM or a trusted machine that can reach the API:

```text
[p]addpath /opt/stock-sum/app/redbot_cogs
[p]load stocksum_report
```

Set the Redbot process environment when the API is not local:

```bash
STOCK_SUM_BASE_URL=http://127.0.0.1:8000
```

The cog uses:

- `POST /v1/social-reports/{profile}/jobs/{mode}`
- `POST /v1/trading-reports/jobs/{mode}`
- `POST /v1/13f-reports/jobs/{mode}`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/artifact`

## Operations

Health and config:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/config/effective
```

Run a social report:

```bash
curl -X POST http://127.0.0.1:8000/v1/social-reports/default/jobs/discord \
  -H 'Content-Type: application/json' \
  -d '{"detail":"minimum"}'
```

Inspect runtime data:

```bash
stock-sum retention status --config config.toml
stock-sum retention prune --dry-run --config config.toml
```

Managed cleanup covers HTTP job artifacts and downloaded media. SQLite source
history and provider response records are preserved.

## 1GB VM Runtime Profile

The shipped defaults favor daemon stability on small VMs:

- HTTP jobs spawn one short-lived child worker process per heavy job, so Python
  heap and parser/LLM memory are reclaimed when the worker exits.
- The daemon keeps only bounded job metadata in memory and reloads old status
  from disk when needed.
- House PTR PDF work defaults to `download_concurrency = 1` and
  `parse_concurrency = 1`.
- Image downloads are disabled by default and retention caps generated data at
  `268435456` bytes.

If a VM is rebooted or the daemon restarts mid-job, stale `queued` or `running`
jobs are marked failed on startup; rerun the report rather than treating the old
job as active.

## Troubleshooting

Missing secrets:

```bash
stock-sum setup check
```

Provider failures:

- Check `XPOZ_API_KEY` and `DEEPSEEK_API_KEY`.
- Confirm outbound HTTPS access from the service host.
- Inspect job status with `GET /v1/jobs/{job_id}` and service logs.

Disk pressure:

- Run `stock-sum retention status`.
- Increase `[retention].max_total_bytes` or run `stock-sum retention prune --apply`.
