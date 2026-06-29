"""Media downloader tests."""

from pathlib import Path

import httpx
import pytest

from stock_sum.config.models import MediaConfig
from stock_sum.media.downloader import MediaDownloadError, MediaDownloader, remote_url_hash
from stock_sum.storage.models import StoredMediaAsset


class FakeMediaRepository:
    def __init__(self):
        self.saved = {}

    async def get_downloaded_media(self, remote_url):
        return self.saved.get(remote_url)

    async def save_downloaded_media(self, media):
        self.saved[media.remote_url] = media


async def test_downloader_saves_image_and_deduplicates(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"image-bytes")

    repository = FakeMediaRepository()
    downloader = MediaDownloader(
        MediaConfig(root_dir=str(tmp_path), max_bytes=100),
        repository,
        transport=httpx.MockTransport(handler),
    )
    asset = StoredMediaAsset(remote_url="https://cdn.example/image.png", media_type="image")

    first = await downloader.download_asset(asset, source_type="x")
    second = await downloader.download_asset(asset, source_type="x")

    assert calls == 1
    assert first.local_path == second.local_path
    assert Path(first.local_path).exists()
    assert first.content_type == "image/png"
    assert first.byte_size == len(b"image-bytes")
    assert first.sha256 is not None
    assert repository.saved[asset.remote_url].remote_url_hash == remote_url_hash(asset.remote_url)


async def test_downloader_rejects_unsupported_content_type(tmp_path) -> None:
    downloader = MediaDownloader(
        MediaConfig(root_dir=str(tmp_path), allowed_content_types=["image/png"]),
        FakeMediaRepository(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"content-type": "text/html"}, content=b"html")),
    )

    with pytest.raises(MediaDownloadError):
        await downloader.download_asset(StoredMediaAsset(remote_url="https://cdn.example/image.png", media_type="image"), source_type="x")


async def test_downloader_rejects_oversized_image(tmp_path) -> None:
    downloader = MediaDownloader(
        MediaConfig(root_dir=str(tmp_path), max_bytes=3),
        FakeMediaRepository(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"large")
        ),
    )

    with pytest.raises(MediaDownloadError):
        await downloader.download_asset(StoredMediaAsset(remote_url="https://cdn.example/image.jpg", media_type="image"), source_type="x")


async def test_downloader_skips_video_assets(tmp_path) -> None:
    repository = FakeMediaRepository()
    downloader = MediaDownloader(MediaConfig(root_dir=str(tmp_path)), repository)
    asset = StoredMediaAsset(remote_url="https://cdn.example/video.mp4", media_type="video")

    result = await downloader.download_asset(asset, source_type="reddit")

    assert result == asset
    assert repository.saved == {}
