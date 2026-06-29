"""Image download support for summary payload media."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
import hashlib
import re

import httpx

from stock_sum.config.models import MediaConfig
from stock_sum.storage.models import StoredDownloadedMedia, StoredMediaAsset
from stock_sum.storage.repository import StorageRepository


class MediaDownloadError(Exception):
    """Raised when a media asset cannot be downloaded."""


class MediaDownloader:
    """Downloads remote image media to deterministic local files."""

    def __init__(
        self,
        config: MediaConfig,
        repository: StorageRepository,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.repository = repository
        self.transport = transport

    async def download_asset(self, asset: StoredMediaAsset, *, source_type: str) -> StoredMediaAsset:
        """Download one image asset if eligible and return an enriched asset."""

        if not _is_image_like_asset(asset):
            return asset

        existing = await self.repository.get_downloaded_media(asset.remote_url)
        if existing is not None:
            return _asset_with_download(asset, existing)

        async with httpx.AsyncClient(timeout=self.config.timeout_seconds, transport=self.transport) as client:
            response = await client.get(asset.remote_url, follow_redirects=True)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in self.config.allowed_content_types:
            raise MediaDownloadError(f"Unsupported media content type: {content_type or 'unknown'}")

        content = response.content
        if len(content) > self.config.max_bytes:
            raise MediaDownloadError(f"Media exceeds configured max_bytes: {len(content)}")

        remote_hash = remote_url_hash(asset.remote_url)
        digest = hashlib.sha256(content).hexdigest()
        local_path = _local_path(
            root_dir=Path(self.config.root_dir),
            source_type=source_type,
            remote_hash=remote_hash,
            content_type=content_type,
            remote_url=asset.remote_url,
        )
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(content)

        downloaded = StoredDownloadedMedia(
            remote_url_hash=remote_hash,
            remote_url=asset.remote_url,
            local_path=local_path.as_posix(),
            content_type=content_type,
            byte_size=len(content),
            sha256=digest,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )
        await self.repository.save_downloaded_media(downloaded)
        return _asset_with_download(asset, downloaded)


def remote_url_hash(remote_url: str) -> str:
    """Return the deterministic storage key for a remote URL."""

    return hashlib.sha256(remote_url.encode("utf-8")).hexdigest()


def _asset_with_download(asset: StoredMediaAsset, downloaded: StoredDownloadedMedia) -> StoredMediaAsset:
    return StoredMediaAsset(
        remote_url=asset.remote_url,
        media_type=asset.media_type,
        raw_metadata=asset.raw_metadata,
        local_path=downloaded.local_path,
        content_type=downloaded.content_type,
        byte_size=downloaded.byte_size,
        sha256=downloaded.sha256,
    )


def _is_image_like_asset(asset: StoredMediaAsset) -> bool:
    media_type = (asset.media_type or "").lower()
    if media_type in {"video", "animated_gif"}:
        return False
    if media_type in {"image", "photo", "thumbnail", "gif"}:
        return True
    return bool(re.search(r"\.(jpg|jpeg|png|gif|webp)(\?|$)", asset.remote_url, re.IGNORECASE))


def _local_path(
    *,
    root_dir: Path,
    source_type: str,
    remote_hash: str,
    content_type: str,
    remote_url: str,
) -> Path:
    extension = _extension_for_content_type(content_type) or Path(urlparse(remote_url).path).suffix.lower() or ".bin"
    return root_dir / source_type / f"{remote_hash}{extension}"


def _extension_for_content_type(content_type: str) -> str | None:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(content_type)
