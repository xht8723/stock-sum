# Single Source Of Truth For Trading Report Limit

## Goals
- Make stock-sum own the default trading report limit of `100`.
- Keep Discord from clipping or defaulting trading report limits.
- Preserve no-max trading report behavior for HTTP and Discord callers.

## Checklist
- Change `TradingReportJobOptions.limit` default to `100`.
- Keep FastAPI request `limit` optional with `ge=1` only.
- Change Redbot `/tradingreport limit` default to `None`.
- Remove Discord-side max trading limit validation.
- Update docs and tests for stock-sum-owned defaults and no clipping.
- Verify with pytest, config validation, and `git diff --check`.
