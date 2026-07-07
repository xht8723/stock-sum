"""Compatibility module for the stock-sum Redbot cog.

The implementation lives in `redbot_cogs.stocksum_report.cog`; this module
aliases that implementation so existing monkeypatch/import paths still affect
the live command class globals.
"""

from __future__ import annotations

import sys

from redbot_cogs.stocksum_report import cog as _cog

sys.modules[__name__] = _cog
