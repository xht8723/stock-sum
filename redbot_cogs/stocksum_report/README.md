# stocksum_report

Red Discord Bot cog that exposes `/socialreport`, `/tradingreport`,
`/13freport`, and `/statistic`, then bridges those slash commands to the local
`stock-sum` HTTP server.

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
/socialreport profile:default detail:minimum
/tradingreport days:30
/13freport issuer:NVIDIA
/statistic mode:social ticker:NVDA days:30
```

`/socialreport` generates X/Reddit social sentiment reports with LLM analysis.
Its `detail` option defaults to `minimum`, which shows only high-importance
posts. Use `medium` for high plus medium, or `full` for all social posts.
`/tradingreport` generates official House PTR trading disclosure reports from
SQLite without LLM analysis; it requires at least one filter such as `name`,
`days`, a transaction-date range, `asset_type`, or `ticker`. Asset type filters
use House codes such as `ST`, `GS`, `OI`, `CS`, and `OT`; ticker filters apply
to `ST` stock rows.
`/13freport` generates official SEC Form 13F holdings reports from the latest
quarterly SEC dataset in SQLite without LLM analysis. It requires at least one
filter such as `manager`, `issuer`, `cik`, `cusip`, `figi`, date range,
`min_value`, or `min_shares`.
`/statistic` generates a PNG chart from existing SQLite data. Use
`mode:social` for X/Reddit sentiment over time or `mode:trading` for House PTR
purchase/sale activity over time. It requires at least one filter such as
`ticker`, `name`, `asset_type`, `days`, or a date range.

The default format for report commands is Discord-specific markdown and is sent
inline in message chunks. Choose `html`, `markdown`, `text`, or `json` to
receive the report as a file attachment instead.
Statistic output is always a PNG file attachment.

Slash commands validate common input mistakes before calling stock-sum:
malformed dates, invalid source names, unsupported report formats, invalid
asset/ticker identifiers, and out-of-range numeric limits return an immediate
private error message.

The cog polls stock-sum job status once per minute while a report is running.

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
