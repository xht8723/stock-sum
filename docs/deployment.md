# Deployment Guide

This guide explains how to deploy `stock-sum` on a regular machine and with
Docker. The current project state includes the HTTP daemon, configuration
validation, models.dev cache, shared SQLite run/index storage, Xpoz X/Reddit
API collectors, and generic Playwright infrastructure.

## Deployment model

A deployment needs:

- Python 3.12 runtime or the provided Docker image.
- A TOML config file.
- Environment variables for secrets.
- Writable `data/` storage for SQLite files, models.dev cache files, and future
  browser profiles.
- Chromium installed through Playwright for future browser-based collectors.

The default daemon command is:

```bash
stock-sum daemon --config stock_sum/config/example.toml --host 0.0.0.0 --port 8000
```

For real deployments, create a separate config file instead of editing the
bundled example in place.

## 1. Bare-machine deployment

Use this path for a VM, workstation, or small server running Windows, Linux, or
macOS.

### 1.1 Clone and prepare the project

```bash
git clone <repo-url> stock-sum
cd stock-sum
python -m venv .venv
```

Activate the environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux or macOS:

```bash
source .venv/bin/activate
```

Install the package.

```bash
python -m pip install --upgrade pip
pip install -e .
```

For a machine that will also run tests:

```bash
pip install -e ".[dev]"
```

### 1.2 Install Playwright browsers

Windows or macOS:

```bash
python -m playwright install chromium
```

Linux:

```bash
python -m playwright install --with-deps chromium
```

If the Linux host does not allow the dependency installer to use `sudo`, install
the OS packages manually according to Playwright's error output, then rerun:

```bash
python -m playwright install chromium
```

### 1.3 Create configuration

Create a local config file:

```bash
stock-sum config init config.toml
```

Edit `config.toml` for the deployment. The most common values to change are:

```toml
[service]
timezone = "America/Vancouver"

[storage]
sqlite_path = "data/stock_sum.sqlite3"

[playwright]
browser = "chromium"
channel = ""
headless = true
timeout_seconds = 30

[providers.xpoz]
api_key_env = "XPOZ_API_KEY"
server_url = "https://mcp.xpoz.ai/mcp"
timeout_seconds = 60

[llm]
provider = "openai"
model = "gpt-4.1-mini"
api_key_env = "OPENAI_API_KEY"
```

Validate the file:

```bash
stock-sum config validate config.toml
```

### 1.4 Configure secrets

Create an environment file from the example:

Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Linux or macOS:

```bash
cp .env.example .env
```

Fill in only the values needed by enabled integrations:

```env
XPOZ_API_KEY=
OPENAI_API_KEY=
SMTP_USERNAME=
SMTP_PASSWORD=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=
```

The Python process does not automatically load `.env` on bare-metal runs. Load
the variables into the process environment before starting the daemon.

Windows PowerShell:

```powershell
$env:XPOZ_API_KEY = "..."
$env:OPENAI_API_KEY = "..."
$env:SMTP_USERNAME = "..."
$env:SMTP_PASSWORD = "..."
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
```

Linux or macOS:

```bash
set -a
. ./.env
set +a
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
```

### 1.5 Initialize cache

Refresh the models.dev cache:

```bash
stock-sum config sync --config config.toml
```

The bundled default profile currently has no collector references. Example
sources live under `[[sources.x_users]]` and `[[sources.subreddits]]`, but are
disabled until you set `enabled = true` in TOML or add sources through the CLI.
X and Reddit sources use Xpoz. Source-list entries resolve to collector IDs
such as `x.aleabitoreddit` and `reddit.wallstreetbets`.

```bash
stock-sum config x-user add aleabitoreddit --config config.toml --profile default
stock-sum config subreddit add wallstreetbets --config config.toml --profile default
stock-sum collect --collector x.aleabitoreddit --config config.toml
stock-sum collect --collector reddit.wallstreetbets --config config.toml
stock-sum collect --profile default --config config.toml
stock-sum run-report --profile default --config config.toml
```

### 1.6 Run the daemon

For local-only access:

```bash
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
```

For access from other machines or a reverse proxy:

```bash
stock-sum daemon --config config.toml --host 0.0.0.0 --port 8000
```

Verify:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/effective
```

### 1.7 Linux systemd service

Create a dedicated directory layout:

```bash
sudo mkdir -p /opt/stock-sum
sudo chown -R "$USER":"$USER" /opt/stock-sum
cp -R . /opt/stock-sum/app
cd /opt/stock-sum/app
python -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install .
python -m playwright install --with-deps chromium
```

Place `config.toml` and `.env` in `/opt/stock-sum/app`, then create
`/etc/systemd/system/stock-sum.service`:

```ini
[Unit]
Description=stock-sum trading report service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/stock-sum/app
EnvironmentFile=/opt/stock-sum/app/.env
ExecStart=/opt/stock-sum/app/.venv/bin/stock-sum daemon --config /opt/stock-sum/app/config.toml --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start and enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-sum
sudo systemctl status stock-sum
journalctl -u stock-sum -f
```

### 1.8 Windows long-running process

For development, run the daemon in an activated PowerShell session:

```powershell
.\.venv\Scripts\Activate.ps1
$env:XPOZ_API_KEY = "..."
$env:OPENAI_API_KEY = "..."
stock-sum daemon --config config.toml --host 127.0.0.1 --port 8000
```

For an unattended Windows machine, use Task Scheduler:

1. Create a task that runs whether the user is logged on or not.
2. Set the working directory to the project folder.
3. Start this program:

```text
powershell.exe
```

4. Use arguments like:

```text
-NoProfile -ExecutionPolicy Bypass -Command "$env:XPOZ_API_KEY='...'; $env:OPENAI_API_KEY='...'; & 'E:\projects\stock-sum\.venv\Scripts\stock-sum.exe' daemon --config 'E:\projects\stock-sum\config.toml' --host 0.0.0.0 --port 8000"
```

If you need many secrets, prefer setting machine/user environment variables
outside the task instead of embedding them in the task arguments.

## 2. Docker deployment

Use Docker when you want a repeatable runtime image with Python dependencies and
Playwright Chromium installed during image build.

### 2.1 Docker image behavior

The provided `Dockerfile`:

- Starts from `python:3.12-slim`.
- Installs the local package.
- Runs `python -m playwright install --with-deps chromium`.
- Exposes port `8000`.
- Declares `/app/data` as the persistent data volume.
- Starts `stock-sum daemon --host 0.0.0.0 --port 8000`.

The provided `docker-compose.yml`:

- Builds the local image.
- Publishes host port `8000` to container port `8000`.
- Mounts `./data:/app/data`.
- Loads `.env` if present.

### 2.2 Docker Compose quick start

Create `.env`:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Build and start:

```bash
docker compose up --build
```

Verify from the host:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/config/effective
```

Stop:

```bash
docker compose down
```

Keep persistent data by leaving `./data` in place. Remove it only if you want to
delete local cache and SQLite files.

### 2.3 Use a custom config with Compose

Create `config.toml` on the host, then override the service command and mount
the file:

```yaml
services:
  stock-sum:
    build: .
    command:
      [
        "stock-sum",
        "daemon",
        "--config",
        "/app/config.toml",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
      ]
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./config.toml:/app/config.toml:ro
    env_file:
      - path: .env
        required: false
```

Validate the config inside the container:

```bash
docker compose run --rm stock-sum stock-sum config validate /app/config.toml
```

Refresh the models.dev cache:

```bash
docker compose run --rm stock-sum stock-sum config sync --config /app/config.toml
```

### 2.4 Manual docker commands

Build:

```bash
docker build -t stock-sum:local .
```

Run with bundled example config:

```bash
docker run --rm \
  --name stock-sum \
  --env-file .env \
  -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  stock-sum:local
```

Run with a custom config:

```bash
docker run --rm \
  --name stock-sum \
  --env-file .env \
  -p 8000:8000 \
  -v "$PWD/data:/app/data" \
  -v "$PWD/config.toml:/app/config.toml:ro" \
  stock-sum:local \
  stock-sum daemon --config /app/config.toml --host 0.0.0.0 --port 8000
```

PowerShell version:

```powershell
docker run --rm `
  --name stock-sum `
  --env-file .env `
  -p 8000:8000 `
  -v "${PWD}\data:/app/data" `
  -v "${PWD}\config.toml:/app/config.toml:ro" `
  stock-sum:local `
  stock-sum daemon --config /app/config.toml --host 0.0.0.0 --port 8000
```

### 2.5 Docker operations

View logs:

```bash
docker compose logs -f stock-sum
```

Restart:

```bash
docker compose restart stock-sum
```

Rebuild after code changes:

```bash
docker compose build --no-cache
docker compose up -d
```

Run one-off CLI commands:

```bash
docker compose run --rm stock-sum stock-sum --help
docker compose run --rm stock-sum stock-sum config validate stock_sum/config/example.toml
```

The production image does not install the `dev` optional dependencies or copy
the test suite. Run tests on the host, or build a separate development image
that installs `.[dev]` and includes `tests/`.

## 3. Reverse proxy notes

When exposing the daemon beyond localhost, put it behind a reverse proxy or
private network boundary. The current API has no authentication middleware.

Minimal Nginx location:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Keep `/config/effective` private because it reveals deployment structure and the
names of secret environment variables.

## 4. Upgrade checklist

For bare-machine deployments:

```bash
git pull
. .venv/bin/activate
pip install -e .
python -m playwright install chromium
stock-sum config validate config.toml
stock-sum config sync --config config.toml
```

Then restart the process or service.

For Docker deployments:

```bash
git pull
docker compose build
docker compose up -d
docker compose logs -f stock-sum
```

## 5. Troubleshooting

Config validation fails:

- Run `stock-sum config validate config.toml`.
- Check TOML quoting. String values passed through `config set` should include
  quotes, for example `"'America/Vancouver'"` in PowerShell.
- Make sure required sections such as `[llm]` exist.

Playwright cannot launch Chromium:

- Run `python -m playwright install chromium`.
- On Linux, run `python -m playwright install --with-deps chromium`.
- In Docker, rebuild the image so the Dockerfile browser install step reruns.

Collection fails with an unsupported collector kind:

- Built-in collector kinds are `x_user_timeline` for Xpoz X collection and
  `reddit_subreddit` for Xpoz Reddit collection.
- For any other future kind, add a source-specific collector implementation and
  register it in the collector factory.
- Add the matching source-specific storage table and mapper before persisting
  that source type.

Collection fails with a missing Xpoz API key:

- Set `XPOZ_API_KEY` in the process environment.
- Keep the TOML value as `api_key_env = "XPOZ_API_KEY"`; do not put
  the real key in config.

The daemon starts but LLM summaries or deliveries do nothing:

- This is expected in the current scaffold. Collection orchestration exists, but
  summarization, rendering, and delivery are not implemented yet.

Port 8000 is already in use:

- Choose another port, for example `--port 8010`.
- With Compose, change the host side of the mapping, for example
  `"8010:8000"`.
