"""House Clerk Periodic Transaction Report collector."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any
from zipfile import ZipFile
import re
import xml.etree.ElementTree as ET

import httpx

from stock_sum.config.models import CollectorConfig
from stock_sum.core.context import RuntimeContext
from stock_sum.core.models import PipelineSectionWarning, RawItem

if TYPE_CHECKING:
    from stock_sum.storage.repository import StorageRepository

HOUSE_PTR_SOURCE_TYPE = "house_ptr_disclosures"


@dataclass(frozen=True)
class HousePtrFiling:
    """Metadata parsed from one House disclosure XML filing."""

    doc_id: str
    year: int
    filing_type: str
    name: str | None
    status: str | None
    state: str | None
    filing_date: str | None
    raw: dict[str, Any]


class HousePtrDisclosureCollector:
    """Collect House PTR filings and their PDF table contents."""

    def __init__(self, *, collector_id: str, collector_config: CollectorConfig) -> None:
        self.collector_id = collector_id
        self.collector_config = collector_config
        self.repository: StorageRepository | None = None
        self.warnings: list[PipelineSectionWarning] = []

    def set_repository(self, repository: StorageRepository) -> None:
        """Attach the active repository so already-stored PDFs can be skipped."""

        self.repository = repository

    async def collect(self, context: RuntimeContext) -> list[RawItem]:
        """Download current-year House PTR filings and extract trade tables."""

        year = effective_house_ptr_year(self.collector_config)
        zip_url = self.collector_config.zip_url_template.format(year=year)
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            archive = await _download_bytes(client, zip_url)
            filings = parse_house_ptr_zip(archive, year=year)
            existing = await self._existing_doc_ids(year=year)
            pending = [filing for filing in filings if filing.doc_id not in existing]
            items = await self._filing_items(client, pending)
        return items

    async def _existing_doc_ids(self, *, year: int) -> set[str]:
        if self.repository is None:
            return set()
        method = getattr(self.repository, "existing_house_ptr_doc_ids", None)
        if method is None:
            return set()
        return set(await method(year=year))

    async def _filing_items(self, client: httpx.AsyncClient, filings: list[HousePtrFiling]) -> list[RawItem]:
        download_semaphore = asyncio.Semaphore(self.collector_config.download_concurrency)
        parse_semaphore = asyncio.Semaphore(self.collector_config.parse_concurrency)

        async def build_item(filing: HousePtrFiling) -> RawItem | None:
            pdf_url = self.collector_config.pdf_url_template.format(year=filing.year, doc_id=filing.doc_id)
            try:
                async with download_semaphore:
                    pdf_bytes = await _download_bytes(client, pdf_url)
                async with parse_semaphore:
                    tables = await asyncio.to_thread(extract_pdf_tables, pdf_bytes)
                extraction_status = "succeeded"
                extraction_error = None
            except Exception as exc:
                tables = []
                extraction_status = "failed"
                extraction_error = str(exc)
                self.warnings.append(
                    PipelineSectionWarning(
                        section="house_ptr",
                        source_id=filing.doc_id,
                        phase="pdf_extract",
                        message=str(exc),
                    )
                )
            trade_rows = normalize_house_ptr_tables(tables)
            return house_ptr_raw_item(
                filing,
                pdf_url=pdf_url,
                tables=tables,
                trade_rows=trade_rows,
                extraction_status=extraction_status,
                extraction_error=extraction_error,
            )

        items = await asyncio.gather(*(build_item(filing) for filing in filings))
        return [item for item in items if item is not None]


def effective_house_ptr_year(config: CollectorConfig) -> int:
    """Return configured year or current UTC year when year is unset/zero."""

    return config.year or datetime.now(timezone.utc).year


def parse_house_ptr_zip(content: bytes, *, year: int) -> list[HousePtrFiling]:
    """Parse House disclosure ZIP content and return PTR filings only."""

    filings: list[HousePtrFiling] = []
    with ZipFile(BytesIO(content)) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(".xml"):
                continue
            try:
                filings.extend(parse_house_filing_xml_entries(archive.read(name), year=year))
            except ET.ParseError:
                continue
    return filings


def parse_house_filing_xml_entries(content: bytes, *, year: int) -> list[HousePtrFiling]:
    """Parse one XML file that may contain one filing or many Member entries."""

    root = ET.fromstring(content)
    candidate_nodes = [node for node in root.iter() if _local_name(node.tag).lower() == "member"]
    if not candidate_nodes:
        candidate_nodes = [root]
    filings: list[HousePtrFiling] = []
    for node in candidate_nodes:
        filing = parse_house_filing_xml(ET.tostring(node, encoding="utf-8"), year=year)
        if filing is not None and filing.filing_type.upper() == "P":
            filings.append(filing)
    return filings


def parse_house_filing_xml(content: bytes, *, year: int) -> HousePtrFiling | None:
    """Parse one House disclosure XML file into filing metadata."""

    root = ET.fromstring(content)
    fields = _flatten_xml(root)
    doc_id = _field(fields, "docid", "documentid", "doc_id")
    filing_type = _field(fields, "filingtype", "filing_type", "type") or ""
    if not doc_id:
        return None
    return HousePtrFiling(
        doc_id=doc_id,
        year=year,
        filing_type=filing_type,
        name=_field(fields, "name", "filername", "membername", "candidate_name") or _compose_name(fields),
        status=_field(fields, "status", "filerstatus", "memberstatus"),
        state=_field(fields, "state", "statedst", "state_dst", "filingstate"),
        filing_date=_field(fields, "filingdate", "date", "datefiled"),
        raw=fields,
    )


def extract_pdf_tables(content: bytes) -> list[list[list[str]]]:
    """Extract tables from PTR PDF bytes with pdfplumber."""

    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is required for House PTR PDF extraction.") from exc

    tables: list[list[list[str]]] = []
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                cleaned = [[_clean_cell(cell) for cell in row] for row in table if row]
                if cleaned:
                    tables.append(cleaned)
    return tables


def normalize_house_ptr_tables(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Convert extracted PDF tables into raw and display-friendly trade rows."""

    rows: list[dict[str, Any]] = []
    for table_index, table in enumerate(tables):
        header_index = _header_index(table)
        headers = table[header_index] if header_index is not None else []
        data_rows = table[(header_index + 1) if header_index is not None else 0 :]
        for row_index, cells in enumerate(data_rows):
            if not any(cell.strip() for cell in cells):
                continue
            by_header = _cells_by_header(headers, cells)
            fields = {
                "asset": _value_by_hint(by_header, "asset", "security", "company", "ticker"),
                "transaction_type": _value_by_hint(by_header, "type", "transaction"),
                "transaction_date": _value_by_hint(by_header, "date"),
                "amount": _value_by_hint(by_header, "amount", "value", "range"),
            }
            if not fields["asset"] or not any(fields[key] for key in ("transaction_type", "transaction_date", "amount")):
                fields = _fallback_fields_from_collapsed_row(cells) or fields
            if not fields["asset"] or not any(fields[key] for key in ("transaction_type", "transaction_date", "amount")):
                continue
            rows.append(
                {
                    "table_index": table_index,
                    "row_index": row_index,
                    "cells": cells,
                    "fields": fields,
                }
            )
    return rows


def house_ptr_raw_item(
    filing: HousePtrFiling,
    *,
    pdf_url: str,
    tables: list[list[list[str]]],
    trade_rows: list[dict[str, Any]],
    extraction_status: str,
    extraction_error: str | None,
) -> RawItem:
    """Create a source-specific raw item for one House PTR filing."""

    return RawItem(
        source_id=filing.doc_id,
        source_type=HOUSE_PTR_SOURCE_TYPE,
        url=pdf_url,
        text=f"{filing.name or 'Unknown filer'} House PTR disclosure {filing.doc_id}",
        metadata={
            "entity_type": "house_ptr_filing",
            "doc_id": filing.doc_id,
            "year": filing.year,
            "name": filing.name,
            "status": filing.status,
            "state": filing.state,
            "filing_date": filing.filing_date,
            "pdf_url": pdf_url,
            "raw_xml": filing.raw,
            "tables": tables,
            "trade_rows": trade_rows,
            "extraction_status": extraction_status,
            "extraction_error": extraction_error,
        },
    )


async def _download_bytes(client: httpx.AsyncClient, url: str) -> bytes:
    response = await client.get(url)
    response.raise_for_status()
    return response.content


def _flatten_xml(root: ET.Element) -> dict[str, str]:
    fields: dict[str, str] = {}
    for element in root.iter():
        text = (element.text or "").strip()
        if not text:
            continue
        key = _normalize_key(_local_name(element.tag))
        fields[key] = text
    return fields


def _field(fields: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = fields.get(_normalize_key(name))
        if value:
            return value
    return None


def _compose_name(fields: Mapping[str, str]) -> str | None:
    parts = [
        _field(fields, "prefix"),
        _field(fields, "first", "firstname", "first_name"),
        _field(fields, "last", "lastname", "last_name"),
        _field(fields, "suffix"),
    ]
    value = " ".join(part for part in parts if part)
    return value or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _normalize_key(value: str) -> str:
    return "".join(char.lower() for char in value if char.isalnum())


def _clean_cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ")
    text = "".join(character if character.isprintable() else " " for character in text)
    return " ".join(text.split())


def _header_index(table: list[list[str]]) -> int | None:
    for index, row in enumerate(table[:5]):
        text = " ".join(cell.lower() for cell in row)
        if any(token in text for token in ("asset", "owner", "transaction", "amount")):
            return index
    return 0 if table else None


def _cells_by_header(headers: list[str], cells: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, cell in enumerate(cells):
        header = headers[index] if index < len(headers) else f"column_{index + 1}"
        result[_normalize_key(header)] = cell
    return result


def _value_by_hint(row: Mapping[str, str], *hints: str) -> str | None:
    normalized_hints = [_normalize_key(hint) for hint in hints]
    for key, value in row.items():
        if value and any(hint in key for hint in normalized_hints):
            return value
    return None


_COLLAPSED_TRADE_PATTERN = re.compile(
    r"^(?P<asset_before>.*?)\s+"
    r"(?P<transaction_type>S(?:\s*\(partial\))?|P(?:\s*\(partial\))?)\s+"
    r"(?P<transaction_date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<notification_date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<tail>.+)$",
    flags=re.IGNORECASE,
)


def _fallback_fields_from_collapsed_row(cells: list[str]) -> dict[str, str] | None:
    """Recover key fields when PDF extraction collapses a PTR row into one cell."""

    text = " ".join(cell for cell in cells if cell).strip()
    if not text:
        return None
    # Most House PTR rows place filing status metadata after the value range.
    # Keeping only the front segment avoids treating descriptions as asset text.
    candidate = re.split(r"\sF\s+[A-Z]\s*:", text, maxsplit=1)[0].strip()
    match = _COLLAPSED_TRADE_PATTERN.match(candidate)
    if not match:
        return None
    tail = match.group("tail")
    amount_values = re.findall(r"\$[\d,]+", tail)
    if len(amount_values) >= 2 and "-" in tail:
        amount = f"{amount_values[0]} - {amount_values[-1]}"
    elif amount_values:
        amount = amount_values[0]
    else:
        amount = ""
    asset_tail = re.sub(r"\$[\d,]+", " ", tail)
    asset_tail = re.sub(r"\s+-\s+", " ", asset_tail)
    asset = _clean_cell(f"{match.group('asset_before')} {asset_tail}")
    asset = re.sub(r"^(?:SP|DC|JT)\s+", "", asset, flags=re.IGNORECASE)
    asset = asset.strip(" ,-")
    if not asset or not amount:
        return None
    return {
        "asset": asset,
        "transaction_type": _clean_cell(match.group("transaction_type")),
        "transaction_date": match.group("transaction_date"),
        "amount": amount,
    }
