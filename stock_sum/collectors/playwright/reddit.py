"""Playwright collector scaffold for subreddit posts."""

from __future__ import annotations

from stock_sum.collectors.base import Collector
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import RawItem


class RedditCollector(Collector):
    """Collects configured subreddit content through Playwright."""

    def __init__(self, collector_id: str, subreddits: list[str]) -> None:
        self.collector_id = collector_id
        self.subreddits = subreddits

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Collect subreddit items."""

        raise NotImplementedError("Reddit Playwright collection is scaffolded only.")
