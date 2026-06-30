"""Build LLM-ready summary input payloads."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from stock_sum.config.models import AppConfig
from stock_sum.core.errors import ConfigurationError
from stock_sum.core.summary_input import (
    SummaryInput,
    SummaryMediaAsset,
    SummaryRedditComment,
    SummaryRedditPost,
    SummaryRedditSubredditSection,
    SummaryXPost,
    SummaryXUserSection,
)
from stock_sum.media.downloader import MediaDownloader
from stock_sum.storage.models import StoredMediaAsset, StoredRedditPost, StoredXPost
from stock_sum.storage.repository import StorageRepository


class SummaryInputBuilder:
    """Assemble collected source data into an LLM-ready payload."""

    def __init__(
        self,
        *,
        config: AppConfig,
        repository: StorageRepository,
        downloader: MediaDownloader | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.downloader = downloader

    async def build(self, *, profile: str, download_images: bool | None = None) -> SummaryInput:
        """Build a source-separated summary input payload."""

        try:
            profile_config = self.config.reports[profile]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown report profile: {profile}") from exc

        generated_at = datetime.now(timezone.utc)
        source_windows = _source_windows(self.config, profile_config.collector_ids, generated_at=generated_at)
        runs = await self.repository.list_collection_runs(profile=profile, limit=20)
        should_download = self.config.media.download_enabled if download_images is None else download_images
        download_errors: list[dict[str, str]] = []

        x_posts = await self.repository.read_x_posts(
            handles=source_windows.handles or None,
            since_posted_at=source_windows.earliest_x_cutoff,
        )
        reddit_posts = await self.repository.read_reddit_posts(
            subreddits=source_windows.subreddits or None,
            since_posted_at=source_windows.earliest_reddit_cutoff,
        )
        x_posts = [post for post in x_posts if source_windows.includes_x(post)]
        reddit_posts = [post for post in reddit_posts if source_windows.includes_reddit(post)]

        x_sections = await self._x_sections(
            x_posts,
            download_images=should_download,
            download_errors=download_errors,
        )
        reddit_sections = await self._reddit_sections(
            reddit_posts,
            download_images=should_download,
            download_errors=download_errors,
        )

        return SummaryInput(
            profile=profile,
            generated_at=generated_at.isoformat(),
            collection_runs=[asdict(run) for run in runs],
            x=x_sections,
            reddit=reddit_sections,
            metadata={
                "x_handles": source_windows.handles,
                "subreddits": source_windows.subreddits,
                "download_images": should_download,
                "download_errors": download_errors,
                "source_windows": source_windows.metadata,
            },
        )

    async def _x_sections(
        self,
        posts: list[StoredXPost],
        *,
        download_images: bool,
        download_errors: list[dict[str, str]],
    ) -> list[SummaryXUserSection]:
        grouped: dict[str, list[SummaryXPost]] = defaultdict(list)
        for post in posts:
            media = await self._summary_media(
                post.media,
                source_type="x",
                download_images=download_images,
                download_errors=download_errors,
            )
            grouped[post.handle].append(
                SummaryXPost(
                    status_id=post.status_id,
                    url=post.url,
                    text=post.text,
                    author_handle=post.author_handle,
                    author_name=post.author_name,
                    posted_at_text=post.posted_at_text,
                    collected_at=post.collected_at,
                    engagement={
                        "reply_count": post.reply_count,
                        "repost_count": post.repost_count,
                        "like_count": post.like_count,
                        "quote_count": post.quote_count,
                        "view_count": post.view_count,
                    },
                    media=media,
                )
            )
        return [SummaryXUserSection(handle=handle, posts=items) for handle, items in sorted(grouped.items())]

    async def _reddit_sections(
        self,
        posts: list[StoredRedditPost],
        *,
        download_images: bool,
        download_errors: list[dict[str, str]],
    ) -> list[SummaryRedditSubredditSection]:
        grouped: dict[str, list[SummaryRedditPost]] = defaultdict(list)
        for post in posts:
            media = await self._summary_media(
                post.media,
                source_type="reddit",
                download_images=download_images,
                download_errors=download_errors,
            )
            grouped[post.subreddit].append(
                SummaryRedditPost(
                    post_id=post.post_id,
                    fullname=post.fullname,
                    title=post.title,
                    author=post.author,
                    url=post.url,
                    permalink=post.permalink,
                    body=post.selftext,
                    created_at_text=post.created_at_text,
                    collected_at=post.collected_at,
                    score=post.score,
                    ups=post.ups,
                    upvote_ratio=post.upvote_ratio,
                    num_comments=post.num_comments,
                    media=media,
                    comments=[
                        SummaryRedditComment(
                            comment_id=comment.comment_id,
                            post_id=comment.post_id,
                            parent_id=comment.parent_id,
                            author=comment.author,
                            body=comment.body,
                            score=comment.score,
                            ups=comment.ups,
                            url=comment.url,
                            created_at_text=comment.created_at_text,
                            depth=comment.depth,
                        )
                        for comment in post.comments
                    ],
                )
            )
        return [
            SummaryRedditSubredditSection(subreddit=subreddit, posts=items)
            for subreddit, items in sorted(grouped.items())
        ]

    async def _summary_media(
        self,
        media: list[StoredMediaAsset],
        *,
        source_type: str,
        download_images: bool,
        download_errors: list[dict[str, str]],
    ) -> list[SummaryMediaAsset]:
        assets: list[SummaryMediaAsset] = []
        for asset in media:
            current = asset
            if download_images and self.downloader is not None:
                try:
                    current = await self.downloader.download_asset(asset, source_type=source_type)
                except Exception as exc:
                    download_errors.append({"remote_url": asset.remote_url, "error": str(exc)})
            assets.append(_summary_media_asset(current))
        return assets


class _SourceWindows:
    def __init__(self, *, generated_at: datetime) -> None:
        self.generated_at = generated_at
        self.x_cutoffs: dict[str, datetime] = {}
        self.reddit_cutoffs: dict[str, datetime] = {}
        self.metadata: dict[str, dict[str, dict[str, Any]]] = {"x": {}, "reddit": {}}

    @property
    def handles(self) -> list[str]:
        return list(self.x_cutoffs)

    @property
    def subreddits(self) -> list[str]:
        return list(self.reddit_cutoffs)

    @property
    def earliest_x_cutoff(self) -> datetime | None:
        return min(self.x_cutoffs.values(), default=None)

    @property
    def earliest_reddit_cutoff(self) -> datetime | None:
        return min(self.reddit_cutoffs.values(), default=None)

    def add_x(self, handle: str, *, lookback_hours: int, fetch_cap: int) -> None:
        normalized = handle.strip().lstrip("@")
        cutoff = self.generated_at - timedelta(hours=lookback_hours)
        self.x_cutoffs[normalized] = cutoff
        self.metadata["x"][normalized] = {
            "lookback_hours": lookback_hours,
            "window_start": cutoff.isoformat(),
            "fetch_cap": fetch_cap,
        }

    def add_reddit(self, subreddit: str, *, lookback_hours: int, fetch_cap: int) -> None:
        normalized = subreddit.strip().strip("/").removeprefix("r/")
        cutoff = self.generated_at - timedelta(hours=lookback_hours)
        self.reddit_cutoffs[normalized] = cutoff
        self.metadata["reddit"][normalized] = {
            "lookback_hours": lookback_hours,
            "window_start": cutoff.isoformat(),
            "fetch_cap": fetch_cap,
        }

    def includes_x(self, post: StoredXPost) -> bool:
        cutoff = self.x_cutoffs.get(post.handle)
        posted_at = _parse_datetime(post.posted_at_text)
        return cutoff is None or (posted_at is not None and posted_at >= cutoff)

    def includes_reddit(self, post: StoredRedditPost) -> bool:
        cutoff = self.reddit_cutoffs.get(post.subreddit)
        posted_at = _parse_datetime(post.created_at_text)
        return cutoff is None or (posted_at is not None and posted_at >= cutoff)


def _source_windows(config: AppConfig, collector_ids: list[str], *, generated_at: datetime) -> _SourceWindows:
    windows = _SourceWindows(generated_at=generated_at)
    for collector_id in collector_ids:
        if collector_id.startswith("x."):
            source_name = collector_id.split(".", 1)[1]
            source = _find_x_source(config, source_name)
            windows.add_x(
                source.handle if source is not None else source_name,
                lookback_hours=source.lookback_hours if source is not None else 24,
                fetch_cap=source.limit if source is not None else 100,
            )
        elif collector_id.startswith("reddit."):
            source_name = collector_id.split(".", 1)[1]
            source = _find_reddit_source(config, source_name)
            windows.add_reddit(
                source.subreddit if source is not None else source_name,
                lookback_hours=source.lookback_hours if source is not None else 24,
                fetch_cap=source.limit if source is not None else 100,
            )
    return windows


def _find_x_source(config: AppConfig, source_name: str):
    for source in config.sources.x_users:
        if source.handle.strip().lstrip("@").lower() == source_name.lower():
            return source
    return None


def _find_reddit_source(config: AppConfig, source_name: str):
    for source in config.sources.subreddits:
        if source.subreddit.strip().strip("/").removeprefix("r/").lower() == source_name.lower():
            return source
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return datetime.fromtimestamp(float(normalized), timezone.utc)
    except (OverflowError, OSError, ValueError):
        pass
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _summary_media_asset(asset: StoredMediaAsset) -> SummaryMediaAsset:
    return SummaryMediaAsset(
        remote_url=asset.remote_url,
        media_type=asset.media_type,
        local_path=asset.local_path,
        content_type=asset.content_type,
        byte_size=asset.byte_size,
        sha256=asset.sha256,
        width=asset.width,
        height=asset.height,
        source_metadata=_safe_media_metadata(asset.raw_metadata),
    )


def _safe_media_metadata(raw_metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "alt_text",
        "bitrate",
        "content_type",
        "display_url",
        "expanded_url",
        "height",
        "media_id",
        "media_key",
        "original_info",
        "sizes",
        "source_field",
        "source_path",
        "width",
    }
    return {key: value for key, value in raw_metadata.items() if key in allowed}
