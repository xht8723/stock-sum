# stocksum_report

Red Discord Bot cog that exposes `/recent_posts`, `/ptr_search`,
`/13f_search`, `/trendings`, `/plot`, `/daily`, `/cancel_daily`, and
`/settings`, then bridges those slash commands to the local `stock-sum` HTTP
server.

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
/recent_posts detail:minimum
/ptr_search days:30
/13f_search issuer:NVIDIA
/trendings
/trendings limit:5 comparison_days:7
/plot mode:social ticker:NVDA days:30
/plot mode:social fuzzy_search:nvidia days:30
/daily time:13:30
/cancel_daily
/settings list
/settings add-x handle:aleabitoreddit
/settings add-reddit subreddit:wallstreetbets
```

`/recent_posts` generates X/Reddit social sentiment reports with LLM analysis.
Its `detail` option defaults to `minimum`, which shows only high-importance
posts. Use `medium` for high plus medium, or `full` for all social posts.
`/ptr_search` generates official House PTR trading disclosure reports from
SQLite without LLM analysis; it requires at least one filter such as `name`,
transaction dates (`days`, `start_date`, `end_date`), filing dates
(`filing_days`, `filing_start_date`, `filing_end_date`), `asset_type`, or
`ticker`. Asset type filters use House codes such as `ST`, `GS`, `OI`, `CS`,
and `OT`; ticker filters apply to `ST` stock rows. If `limit` is omitted,
stock-sum applies its server-side default.
`/13f_search` generates official SEC Form 13F holdings reports from the latest
quarterly SEC dataset in SQLite without LLM analysis. It requires at least one
filter such as `manager`, `issuer`, `cik`, `cusip`, `figi`, date range,
`min_value`, or `min_shares`. If `limit` is omitted, stock-sum applies its
server-side default.
`/trendings` generates a concise Adanos trendings report for Reddit Stocks and
X Stocks. It queries trending stocks and trending sectors for the latest
24-hour UTC window. `limit` controls displayed trend rows and comparison
tickers; notable-change history can be overridden with `comparison_days` and
defaults to 7. stock-sum fetches the provider maximum so SQLite keeps more
history.
`/plot` generates a PNG chart from existing SQLite data. Use
`mode:social` for X/Reddit sentiment over time or `mode:trading` for House PTR
purchase/sale activity over time. It requires at least one filter such as
`ticker`, `fuzzy_search`, `name`, `asset_type`, `days`, or a date range.
Use `fuzzy_search` instead of `ticker` to choose from numbered emoji matches:
social mode matches stored LLM tags, while trading mode matches House PTR asset
names. Do not provide both `ticker` and `fuzzy_search`.

Report commands always use Discord-specific markdown and are sent inline in
message chunks.
Plot output is always a PNG file attachment.
`/daily` stores a per-user UTC delivery time in `HH:MM` format and sends one
daily DM containing `/trendings`, `/recent_posts` default output, and
the trading report's internal `collected_days:1` output for PTR disclosures
first discovered during the rolling last 24 hours. Photo-scanned filings are
shown with an explicit warning and their official PDF link. Report jobs
start 30 minutes before the configured UTC time; if the configured time is
already inside that 30-minute window, the next scheduler check starts the jobs
immediately. The cog checks schedules locally; stock-sum does not run outbound
delivery. `/cancel_daily` disables the caller's subscription.

Slash commands validate common input mistakes before calling stock-sum:
malformed dates, invalid source names, invalid asset/ticker identifiers, and
out-of-range numeric limits return an immediate validation error message.

The cog polls stock-sum job status once per minute while a report is running.
Report and plot commands post publicly. Daily reports are sent by DM. Source
mutation settings commands are restricted to Redbot owners and respond
privately.

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
