"""LLM-ready summary input payload models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PayloadMode = str


@dataclass(frozen=True)
class SummaryMediaAsset:
    """Media asset in a summary input payload."""

    remote_url: str
    media_type: str | None
    local_path: str | None = None
    content_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SummaryXPost:
    """X post in a summary input payload."""

    status_id: str
    url: str | None
    text: str
    author_handle: str | None
    author_name: str | None
    posted_at_text: str | None
    collected_at: str
    engagement: dict[str, int | None]
    media: list[SummaryMediaAsset] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryXUserSection:
    """Grouped X posts for one user."""

    handle: str
    posts: list[SummaryXPost] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryRedditComment:
    """Reddit comment linked to one post."""

    comment_id: str
    post_id: str
    parent_id: str | None
    author: str | None
    body: str
    score: int | None
    ups: int | None
    url: str | None
    created_at_text: str | None
    depth: int | None


@dataclass(frozen=True)
class SummaryRedditPost:
    """Reddit post in a summary input payload."""

    post_id: str
    fullname: str | None
    title: str
    author: str | None
    url: str | None
    permalink: str | None
    body: str
    created_at_text: str | None
    collected_at: str
    score: int | None
    ups: int | None
    upvote_ratio: float | None
    num_comments: int | None
    media: list[SummaryMediaAsset] = field(default_factory=list)
    comments: list[SummaryRedditComment] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryRedditSubredditSection:
    """Grouped Reddit posts for one subreddit."""

    subreddit: str
    posts: list[SummaryRedditPost] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryInput:
    """Complete LLM-ready summary input payload."""

    profile: str
    generated_at: str
    collection_runs: list[dict[str, Any]]
    x: list[SummaryXUserSection] = field(default_factory=list)
    reddit: list[SummaryRedditSubredditSection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(
        self,
        *,
        mode: PayloadMode = "full",
        max_images_per_post: int = 3,
        max_images_total: int = 20,
    ) -> dict[str, Any]:
        """Return a JSON-serializable payload."""

        if mode == "full":
            return asdict(self)
        if mode == "compact":
            return _drop_empty(_compact_payload(self, max_images_per_post=max_images_per_post, max_images_total=max_images_total))
        if mode == "vision":
            payload = _compact_payload(self, max_images_per_post=max_images_per_post, max_images_total=max_images_total)
            payload["vision"] = _vision_manifest(payload)
            return _drop_empty(payload)
        raise ValueError(f"Unsupported payload mode: {mode}")


def _compact_payload(
    summary_input: SummaryInput,
    *,
    max_images_per_post: int,
    max_images_total: int,
) -> dict[str, Any]:
    media_registry = _MediaRegistry(max_images_per_post=max_images_per_post, max_images_total=max_images_total)
    x_sections = [_compact_x_section(section, media_registry) for section in summary_input.x]
    reddit_sections = [_compact_reddit_section(section, media_registry) for section in summary_input.reddit]
    metadata = {
        "x_handles": summary_input.metadata.get("x_handles"),
        "subreddits": summary_input.metadata.get("subreddits"),
        "download_images": summary_input.metadata.get("download_images"),
        "download_errors": summary_input.metadata.get("download_errors"),
        "collection_run_count": len(summary_input.collection_runs),
    }
    return {
        "profile": summary_input.profile,
        "generated_at": summary_input.generated_at,
        "sources": {
            "x": x_sections,
            "reddit": reddit_sections,
        },
        "media": media_registry.media,
        "metadata": metadata,
    }


def _compact_x_section(section: SummaryXUserSection, media_registry: "_MediaRegistry") -> dict[str, Any]:
    posts: list[dict[str, Any]] = []
    for index, post in enumerate(section.posts, start=1):
        post_id = f"x{index}"
        media_ids = media_registry.add_for_post(post.media, source="x", source_ref=post_id)
        posts.append(
            {
                "id": post_id,
                "source_id": post.status_id,
                "url": post.url,
                "time": post.posted_at_text,
                "author": post.author_handle or post.author_name,
                "text": post.text,
                "engagement": _drop_empty(
                    {
                        "replies": post.engagement.get("reply_count"),
                        "reposts": post.engagement.get("repost_count"),
                        "likes": post.engagement.get("like_count"),
                        "quotes": post.engagement.get("quote_count"),
                        "views": post.engagement.get("view_count"),
                    }
                ),
                "media": media_ids,
            }
        )
    return {"handle": section.handle, "posts": posts}


def _compact_reddit_section(section: SummaryRedditSubredditSection, media_registry: "_MediaRegistry") -> dict[str, Any]:
    posts: list[dict[str, Any]] = []
    for index, post in enumerate(section.posts, start=1):
        post_id = f"r{index}"
        media_ids = media_registry.add_for_post(post.media, source="reddit", source_ref=post_id)
        posts.append(
            {
                "id": post_id,
                "source_id": post.post_id,
                "title": post.title,
                "url": post.permalink or post.url,
                "time": post.created_at_text,
                "author": post.author,
                "body": post.body,
                "score": post.score,
                "ups": post.ups,
                "upvote_ratio": post.upvote_ratio,
                "num_comments": post.num_comments,
                "media": media_ids,
                "comments": [
                    {
                        "id": f"{post_id}.c{comment_index}",
                        "source_id": comment.comment_id,
                        "parent": comment.parent_id,
                        "author": comment.author,
                        "body": comment.body,
                        "score": comment.score,
                        "ups": comment.ups,
                        "time": comment.created_at_text,
                        "depth": comment.depth,
                    }
                    for comment_index, comment in enumerate(post.comments, start=1)
                ],
            }
        )
    return {"subreddit": section.subreddit, "posts": posts}


class _MediaRegistry:
    def __init__(self, *, max_images_per_post: int, max_images_total: int) -> None:
        self.max_images_per_post = max(0, max_images_per_post)
        self.max_images_total = max(0, max_images_total)
        self.media: dict[str, dict[str, Any]] = {}
        self._by_url: dict[str, str] = {}
        self._image_count = 0

    def add_for_post(self, assets: list[SummaryMediaAsset], *, source: str, source_ref: str) -> list[str]:
        selected = self._select_assets(assets)
        media_ids: list[str] = []
        for asset in selected:
            existing = self._by_url.get(asset.remote_url)
            if existing is not None:
                media_ids.append(existing)
                continue
            if _is_image_media(asset):
                if self._image_count >= self.max_images_total:
                    continue
                self._image_count += 1
            media_id = f"m{len(self.media) + 1}"
            self._by_url[asset.remote_url] = media_id
            self.media[media_id] = _compact_media_asset(asset, source=source, source_ref=source_ref)
            media_ids.append(media_id)
        return media_ids

    def _select_assets(self, assets: list[SummaryMediaAsset]) -> list[SummaryMediaAsset]:
        image_assets = [asset for asset in assets if _is_image_media(asset)]
        full_images = [asset for asset in image_assets if asset.media_type not in {"thumbnail"}]
        selected = full_images or image_assets
        return selected[: self.max_images_per_post]


def _compact_media_asset(asset: SummaryMediaAsset, *, source: str, source_ref: str) -> dict[str, Any]:
    source_metadata = {
        key: value
        for key, value in asset.source_metadata.items()
        if key in {"alt_text", "display_url", "expanded_url", "source_field", "source_path"}
    }
    return {
        "source": source,
        "source_ref": source_ref,
        "kind": asset.media_type,
        "remote_url": asset.remote_url,
        "local_path": asset.local_path,
        "width": asset.width,
        "height": asset.height,
        "alt_text": source_metadata.get("alt_text"),
        "source_hint": source_metadata.get("source_field") or source_metadata.get("source_path"),
    }


def _vision_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    attachments = [
        {
            "id": media_id,
            "source": media["source"],
            "source_ref": media["source_ref"],
            "kind": media.get("kind"),
            "local_path": media.get("local_path"),
            "remote_url": media.get("remote_url"),
        }
        for media_id, media in payload.get("media", {}).items()
        if _is_image_kind(media.get("kind"))
    ]
    return {
        "instruction": "Interleave each image with the text item identified by source_ref. Prefer local_path when available; otherwise use remote_url.",
        "attachments": attachments,
    }


def _is_image_media(asset: SummaryMediaAsset) -> bool:
    return _is_image_kind(asset.media_type)


def _is_image_kind(kind: Any) -> bool:
    return str(kind or "").lower() in {"image", "photo", "thumbnail", "gif"}


def _drop_empty(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned = {key: _drop_empty(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, {}, [])}
    if isinstance(value, list):
        return [_drop_empty(item) for item in value if _drop_empty(item) not in (None, {}, [])]
    return value
