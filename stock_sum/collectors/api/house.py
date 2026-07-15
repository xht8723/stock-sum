"""House Clerk Periodic Transaction Report collector."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
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

HOUSE_ASSET_TYPE_LABELS = {
    "ST": "Stocks, including ADRs",
    "GS": "Government Securities and Agency Debt",
    "OI": "Ownership Interest (Holding Investments)",
    "CS": "Corporate Securities (Bonds and Notes)",
    "OT": "Other",
    "HN": "Hedge Funds & Private Equity Funds (non-EIF)",
    "OP": "Options",
    "PS": "Stock, not publicly traded",
    "VA": "Variable Annuity",
    "CT": "Cryptocurrency",
    "OL": "Ownership Interest in a business where the owner is engaged in its trade or operations",
    "RS": "Restricted Stock Units (RSUs)",
    "AB": "Asset-Backed Securities",
}


@dataclass(frozen=True)
class HousePtrFiling:
    """Metadata parsed from one House disclosure XML filing."""

    doc_id: str
    year: int
    filing_type: str
    name: str | None = None
    prefix: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    suffix: str | None = None
    display_name: str | None = None
    name_normalized: str | None = None
    status: str | None = None
    state: str | None = None
    filing_date: str | None = None
    filing_date_utc: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HousePtrPdfExtraction:
    """Text and table signals extracted from a House PTR PDF."""

    tables: list[list[list[str]]]
    page_count: int
    text_page_count: int

    @property
    def has_text(self) -> bool:
        return self.text_page_count > 0


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
                    extraction = await asyncio.to_thread(extract_pdf_content, pdf_bytes)
                tables = extraction.tables
                extraction_status, trade_rows, extraction_warnings = classify_house_ptr_extraction(
                    extraction,
                    pdf_url=pdf_url,
                )
                extraction_error = None
                extraction_metadata = {
                    "page_count": extraction.page_count,
                    "text_page_count": extraction.text_page_count,
                    "table_count": len(tables),
                }
            except Exception as exc:
                tables = []
                trade_rows = []
                extraction_status = "failed"
                extraction_error = str(exc)
                extraction_warnings = [
                    {
                        "code": "house_ptr_extraction_failed",
                        "message": str(exc),
                        "source_url": pdf_url,
                    }
                ]
                extraction_metadata = {}
                self.warnings.append(
                    PipelineSectionWarning(
                        section="house_ptr",
                        source_id=filing.doc_id,
                        phase="pdf_extract",
                        message=str(exc),
                    )
                )
            return house_ptr_raw_item(
                filing,
                pdf_url=pdf_url,
                tables=tables,
                trade_rows=trade_rows,
                extraction_status=extraction_status,
                extraction_error=extraction_error,
                extraction_warnings=extraction_warnings,
                extraction_metadata=extraction_metadata,
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
    prefix = _field(fields, "prefix")
    first_name = _field(fields, "first", "firstname", "first_name")
    last_name = _field(fields, "last", "lastname", "last_name")
    suffix = _field(fields, "suffix")
    display_name = _field(fields, "name", "filername", "membername", "candidate_name") or compose_house_display_name(
        prefix=prefix,
        first_name=first_name,
        last_name=last_name,
        suffix=suffix,
    )
    filing_date = _field(fields, "filingdate", "date", "datefiled")
    return HousePtrFiling(
        doc_id=doc_id,
        year=year,
        filing_type=filing_type,
        name=display_name,
        prefix=prefix,
        first_name=first_name,
        last_name=last_name,
        suffix=suffix,
        display_name=display_name,
        name_normalized=normalize_house_name(display_name),
        status=_field(fields, "status", "filerstatus", "memberstatus"),
        state=_field(fields, "state", "statedst", "state_dst", "filingstate"),
        filing_date=filing_date,
        filing_date_utc=normalize_house_date(filing_date),
        raw=fields,
    )


def extract_pdf_tables(content: bytes) -> list[list[list[str]]]:
    """Extract tables from PTR PDF bytes with pdfplumber."""

    return extract_pdf_content(content).tables


def extract_pdf_content(content: bytes) -> HousePtrPdfExtraction:
    """Extract table data and detect whether a PTR PDF has selectable text."""

    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("pdfplumber is required for House PTR PDF extraction.") from exc

    tables: list[list[list[str]]] = []
    page_count = 0
    text_page_count = 0
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            page_count += 1
            if (page.extract_text() or "").strip():
                text_page_count += 1
            for table in page.extract_tables() or []:
                cleaned = [[_clean_cell(cell) for cell in row] for row in table if row]
                if cleaned:
                    tables.append(cleaned)
    return HousePtrPdfExtraction(
        tables=tables,
        page_count=page_count,
        text_page_count=text_page_count,
    )


def classify_house_ptr_extraction(
    extraction: HousePtrPdfExtraction,
    *,
    pdf_url: str,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Classify an extracted PDF and return status, trade rows, and warnings."""

    trade_rows = normalize_house_ptr_tables(extraction.tables)
    if trade_rows:
        return "succeeded", trade_rows, []
    if not extraction.has_text and not extraction.tables:
        return (
            "photo_scanned",
            [],
            [
                {
                    "code": "house_ptr_photo_scanned",
                    "message": "The filing is photo scanned",
                    "source_url": pdf_url,
                }
            ],
        )
    return (
        "unparsed",
        [],
        [
            {
                "code": "house_ptr_unparsed",
                "message": "No House PTR transactions could be extracted from this filing.",
                "source_url": pdf_url,
            }
        ],
    )


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
            fields.update(parse_house_asset_metadata(fields.get("asset")))
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
    extraction_warnings: list[dict[str, Any]] | None = None,
    extraction_metadata: dict[str, Any] | None = None,
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
            "prefix": filing.prefix,
            "first_name": filing.first_name,
            "last_name": filing.last_name,
            "suffix": filing.suffix,
            "display_name": filing.display_name,
            "name_normalized": filing.name_normalized,
            "status": filing.status,
            "state": filing.state,
            "filing_date": filing.filing_date,
            "filing_date_utc": filing.filing_date_utc,
            "pdf_url": pdf_url,
            "raw_xml": filing.raw,
            "tables": tables,
            "trade_rows": trade_rows,
            "extraction_status": extraction_status,
            "extraction_error": extraction_error,
            "extraction_warnings": extraction_warnings or [],
            "extraction_metadata": extraction_metadata or {},
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


def compose_house_display_name(
    *,
    prefix: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    suffix: str | None = None,
) -> str | None:
    """Compose a display name from official House XML name fields."""

    parts = [prefix, first_name, last_name, suffix]
    value = " ".join(part for part in parts if part)
    return value or None


def normalize_house_name(value: str | None) -> str | None:
    """Normalize a filer name for fuzzy search."""

    if not value:
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return re.sub(r"\s+", " ", normalized) or None


def normalize_house_date(value: str | None) -> str | None:
    """Normalize common House date strings to UTC ISO date-time text."""

    if not value:
        return None
    text = value.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat()


def normalize_house_transaction_action(value: str | None) -> str | None:
    """Normalize House transaction codes into queryable action values."""

    if not value:
        return None
    normalized = value.strip().lower()
    if normalized.startswith("p"):
        return "purchase"
    if normalized.startswith("s"):
        if "partial" in normalized:
            return "sell_partial"
        return "sell"
    if normalized.startswith("purchase"):
        return "purchase"
    if normalized.startswith("sale") or normalized.startswith("sell"):
        return "sell"
    return normalized or None


def parse_house_asset_metadata(asset: str | None) -> dict[str, str | None]:
    """Extract queryable House asset type and stock ticker metadata."""

    if not asset:
        return {"asset_type_code": None, "asset_type_label": None, "stock_ticker": None}
    type_match = re.search(r"\[([A-Za-z]{2})\]\s*$", asset.strip())
    asset_type_code = type_match.group(1).upper() if type_match else None
    asset_type_label = HOUSE_ASSET_TYPE_LABELS.get(asset_type_code, "Unknown") if asset_type_code else None
    stock_ticker = None
    if asset_type_code == "ST":
        before_type = asset[: type_match.start()].strip() if type_match else asset.strip()
        ticker_match = re.search(r"\(([A-Za-z][A-Za-z0-9.$/\-]*)\)\s*$", before_type)
        if ticker_match:
            stock_ticker = ticker_match.group(1).upper()
    return {
        "asset_type_code": asset_type_code,
        "asset_type_label": asset_type_label,
        "stock_ticker": stock_ticker,
    }


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
