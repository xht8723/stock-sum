# stocksum_report

Red Discord Bot cog that exposes `/report` and bridges to the local `stock-sum`
HTTP server.

## Environment

The Redbot process may set:

```bash
STOCK_SUM_BASE_URL=http://127.0.0.1:8000
```

`STOCK_SUM_BASE_URL` is optional and defaults to `http://127.0.0.1:8000`.
The stock-sum HTTP API is open to non-blacklisted clients.

## Load

From Red:

```text
[p]addpath /opt/stock-sum/app/redbot_cogs
[p]load stocksum_report
```

Then sync slash commands with Red's slash-command management command and run:

```text
/report profile:default
```

The default format is Discord-specific markdown and is sent inline when it fits
in one Discord message. Choose `html`, `markdown`, `text`, or `json` to receive
the report as a file attachment instead.

## Management Commands

The cog also exposes a `/stocksum` command group that calls the local management
API:

```text
/stocksum profiles list
/stocksum sources list
/stocksum sources add-x handle:aleabitoreddit limit:100 lookback_hours:24
/stocksum sources add-reddit subreddit:wallstreetbets limit:100 lookback_hours:24
/stocksum llm providers
/stocksum llm select provider:deepseek
/stocksum secrets list
/stocksum secrets set name:XPOZ_API_KEY value:...
/stocksum collect profile profile:default
/stocksum setup check
/stocksum retention status
```

Read-only commands can be used by normal Discord users. Commands that mutate
config, write secrets, or prune data are restricted to Redbot owners and respond
privately.
