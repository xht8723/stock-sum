"""SEC Form 13F dataset collector."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO, TextIOWrapper
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urljoin
from zipfile import ZipFile
import csv
import hashlib
import re

import httpx

from stock_sum.collectors.base import Collector
from stock_sum.config.models import CollectorConfig, Sec13FSourceConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import RawItem

SEC_13F_SOURCE_TYPE = "sec_13f_dataset"
SEC_13F_COLLECTOR_ID = "sec.13f"

SEC_13F_TABLES = {
    "SUBMISSION": "submissions",
    "COVERPAGE": "coverpages",
    "OTHERMANAGER": "other_managers",
    "SIGNATURE": "signatures",
    "SUMMARYPAGE": "summary_pages",
    "OTHERMANAGER2": "other_managers2",
    "INFOTABLE": "info_tables",
}


@dataclass(frozen=True)
class Sec13FLatestDataset:
    """Latest SEC 13F ZIP metadata discovered from the SEC page."""

    label: str
    url: str

    @property
    def dataset_id(self) -> str:
        digest = hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]
        slug = re.sub(r"[^a-z0-9]+", "-", self.label.lower()).strip("-")
        return f"{slug}-{digest}" if slug else digest


class Sec13FDatasetCollector(Collector):
    """Download and parse the latest SEC 13F quarterly dataset."""

    def __init__(self, *, collector_id: str, collector_config: CollectorConfig, source_config: Sec13FSourceConfig) -> None:
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.source_config = source_config

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Collect the latest SEC 13F data ZIP as one dataset raw item."""

        timeout = httpx.Timeout(self.source_config.download_timeout_seconds)
        headers = {"User-Agent": self.source_config.user_agent}
        async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
            page_response = await client.get(self.source_config.page_url)
            page_response.raise_for_status()
            latest = discover_latest_13f_dataset(page_response.text, base_url=self.source_config.page_url)
            zip_response = await client.get(latest.url)
            zip_response.raise_for_status()
        parsed = parse_sec_13f_zip(zip_response.content)
        sha256 = hashlib.sha256(zip_response.content).hexdigest()
        return [
            sec_13f_raw_item(
                latest=latest,
                rows_by_table=parsed,
                sha256=sha256,
                byte_size=len(zip_response.content),
            )
        ]


def sec_13f_source_to_collector_config(source: Sec13FSourceConfig) -> CollectorConfig:
    """Convert long-list source settings into a generic collector config."""

    return CollectorConfig(kind=SEC_13F_SOURCE_TYPE, enabled=source.enabled)


def discover_latest_13f_dataset(html: str, *, base_url: str) -> Sec13FLatestDataset:
    """Return the first ZIP link in the SEC 13F data downloads table."""

    downloads_start = html.lower().find("data downloads")
    search_area = html[downloads_start:] if downloads_start >= 0 else html
    pattern = re.compile(r'<a\s+[^>]*href=["\'](?P<href>[^"\']+\.zip)["\'][^>]*>(?P<label>.*?)</a>', re.IGNORECASE | re.DOTALL)
    match = pattern.search(search_area)
    if not match:
        raise ValueError("Could not find a 13F ZIP download link on the SEC page.")
    label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", match.group("label"))).strip()
    return Sec13FLatestDataset(label=label, url=urljoin(base_url, match.group("href")))


def parse_sec_13f_zip(content: bytes) -> dict[str, list[dict[str, str]]]:
    """Parse all SEC 13F TSV files in a ZIP archive."""

    rows_by_table: dict[str, list[dict[str, str]]] = {value: [] for value in SEC_13F_TABLES.values()}
    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith((".tsv", ".txt")):
                continue
            table_key = _table_key_from_filename(name)
            if table_key is None:
                continue
            with archive.open(name) as handle:
                text = TextIOWrapper(handle, encoding="utf-8-sig", newline="")
                reader = csv.DictReader(text, delimiter="\t")
                rows_by_table[table_key].extend(_clean_row(row) for row in reader)
    return rows_by_table


def sec_13f_raw_item(
    *,
    latest: Sec13FLatestDataset,
    rows_by_table: dict[str, list[dict[str, str]]],
    sha256: str,
    byte_size: int,
) -> RawItem:
    """Build a RawItem for source-aware SEC 13F persistence."""

    row_counts = {table: len(rows) for table, rows in rows_by_table.items()}
    return RawItem(
        source_id=latest.dataset_id,
        source_type=SEC_13F_SOURCE_TYPE,
        url=latest.url,
        text=latest.label,
        metadata={
            "entity_type": "sec_13f_dataset",
            "dataset_id": latest.dataset_id,
            "label": latest.label,
            "download_url": latest.url,
            "sha256": sha256,
            "byte_size": byte_size,
            "row_counts": row_counts,
            "rows_by_table": rows_by_table,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def normalize_sec_date(value: Any) -> str | None:
    """Normalize SEC DD-MON-YYYY dates into ISO dates."""

    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.title(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_sec_name(value: Any) -> str:
    """Normalize names for case-insensitive contains queries."""

    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def sec_filing_url(cik: Any, accession_number: Any) -> str | None:
    """Return the SEC archive URL for an accession when enough fields exist."""

    cik_text = str(cik or "").lstrip("0").strip()
    accession = str(accession_number or "").strip()
    if not cik_text or not accession:
        return None
    compact = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik_text}/{compact}/{accession}.txt"


def _table_key_from_filename(name: str) -> str | None:
    stem = PurePosixPath(name).stem.upper()
    normalized = re.sub(r"[^A-Z0-9]", "", stem)
    for prefix, table in SEC_13F_TABLES.items():
        if normalized == prefix or normalized.endswith(prefix):
            return table
    return None


def _clean_row(row: dict[str, Any]) -> dict[str, str]:
    return {str(key or "").strip().upper(): str(value or "").strip() for key, value in row.items() if key}

