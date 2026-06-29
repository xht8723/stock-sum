# stocksum_report

Red Discord Bot cog that exposes `/report` and bridges to the local `stock-sum`
HTTP server.

## Environment

The Redbot process needs:

```bash
STOCK_SUM_HTTP_TOKEN=the_same_token_used_by_stock_sum
STOCK_SUM_BASE_URL=http://127.0.0.1:8000
```

`STOCK_SUM_BASE_URL` is optional and defaults to `http://127.0.0.1:8000`.

## Load

From Red:

```text
[p]addpath /opt/stock-sum/app/redbot_cogs
[p]load stocksum_report
```

Then sync slash commands with Red's slash-command management command and run:

```text
/report profile:default format:html
```
