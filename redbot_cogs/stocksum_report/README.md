# stocksum_report

Red Discord Bot cog that exposes `/socialreport`, `/tradingreport`,
`/13freport`, `/statistic`, and `/settings`, then bridges those slash commands
to the local `stock-sum` HTTP server.

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
/socialreport detail:minimum
/tradingreport days:30
/13freport issuer:NVIDIA
/statistic mode:social ticker:NVDA days:30
/statistic mode:social fuzzy_search:nvidia days:30
/settings list
/settings add-x handle:aleabitoreddit
/settings add-reddit subreddit:wallstreetbets
```

`/socialreport` generates X/Reddit social sentiment reports with LLM analysis.
Its `detail` option defaults to `minimum`, which shows only high-importance
posts. Use `medium` for high plus medium, or `full` for all social posts.
`/tradingreport` generates official House PTR trading disclosure reports from
SQLite without LLM analysis; it requires at least one filter such as `name`,
`days`, a transaction-date range, `asset_type`, or `ticker`. Asset type filters
use House codes such as `ST`, `GS`, `OI`, `CS`, and `OT`; ticker filters apply
to `ST` stock rows. If `limit` is omitted, stock-sum applies its server-side
default.
`/13freport` generates official SEC Form 13F holdings reports from the latest
quarterly SEC dataset in SQLite without LLM analysis. It requires at least one
filter such as `manager`, `issuer`, `cik`, `cusip`, `figi`, date range,
`min_value`, or `min_shares`. If `limit` is omitted, stock-sum applies its
server-side default.
`/statistic` generates a PNG chart from existing SQLite data. Use
`mode:social` for X/Reddit sentiment over time or `mode:trading` for House PTR
purchase/sale activity over time. It requires at least one filter such as
`ticker`, `fuzzy_search`, `name`, `asset_type`, `days`, or a date range.
Use `fuzzy_search` instead of `ticker` to choose from numbered emoji matches:
social mode matches stored LLM tags, while trading mode matches House PTR asset
names. Do not provide both `ticker` and `fuzzy_search`.

Report commands always use Discord-specific markdown and are sent inline in
message chunks.
Statistic output is always a PNG file attachment.

Slash commands validate common input mistakes before calling stock-sum:
malformed dates, invalid source names, invalid asset/ticker identifiers, and
out-of-range numeric limits return an immediate validation error message.

The cog polls stock-sum job status once per minute while a report is running.
Report and statistic commands post publicly. Source mutation settings commands
are restricted to Redbot owners and respond privately.

## Settings Commands

The cog exposes a small `/settings` command group for X/Reddit social sources:

```text
/settings list
/settings add-x handle:aleabitoreddit
/settings add-x handle:@aleabitoreddit
/settings remove-x handle:aleabitoreddit
/settings add-reddit subreddit:wallstreetbets
/settings add-reddit subreddit:r/wallstreetbets
/settings remove-reddit subreddit:wallstreetbets
```

`/settings list` can be used by normal Discord users and labels X users and
subreddits separately. Add/remove commands are restricted to Redbot owners and
respond privately. LLM provider changes and secret updates are handled through
the stock-sum CLI/env files, not Discord.
