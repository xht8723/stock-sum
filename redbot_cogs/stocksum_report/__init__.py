"""Redbot loader for the stock-sum report cog."""

from __future__ import annotations

from .cog import StockSumReport


async def setup(bot):
    """Load the cog into Red."""

    await bot.add_cog(StockSumReport(bot))
