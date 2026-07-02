"""House PTR disclosure collector tests."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from stock_sum.collectors.api.house import (
    HousePtrDisclosureCollector,
    HousePtrFiling,
    house_ptr_raw_item,
    normalize_house_ptr_tables,
    parse_house_asset_metadata,
    parse_house_ptr_zip,
)
from stock_sum.config.models import CollectorConfig


def test_parse_house_ptr_zip_filters_periodic_transaction_reports() -> None:
    content = _zip_bytes(
        {
            "ptr.xml": """
                <Filing>
                  <DocID>20024228</DocID>
                  <FilingType>P</FilingType>
                  <Name>Jane Doe</Name>
                  <Status>Member</Status>
                  <State>CA</State>
                  <FilingDate>2026-06-30</FilingDate>
                </Filing>
            """,
            "annual.xml": """
                <Filing>
                  <DocID>100</DocID>
                  <FilingType>A</FilingType>
                  <Name>Ignored</Name>
                </Filing>
            """,
        }
    )

    filings = parse_house_ptr_zip(content, year=2026)

    assert [filing.doc_id for filing in filings] == ["20024228"]
    assert filings[0].name == "Jane Doe"
    assert filings[0].status == "Member"
    assert filings[0].state == "CA"


def test_parse_house_ptr_zip_handles_live_aggregate_member_xml() -> None:
    content = _zip_bytes(
        {
            "2026FD.xml": """
                <FinancialDisclosure>
                  <Member>
                    <Prefix>Hon.</Prefix>
                    <Last>Alford</Last>
                    <First>Mark</First>
                    <Suffix />
                    <FilingType>P</FilingType>
                    <StateDst>MO04</StateDst>
                    <Year>2026</Year>
                    <FilingDate>3/31/2026</FilingDate>
                    <DocID>20034201</DocID>
                  </Member>
                  <Member>
                    <Last>Example</Last>
                    <First>Annual</First>
                    <FilingType>A</FilingType>
                    <StateDst>CA01</StateDst>
                    <Year>2026</Year>
                    <FilingDate>4/1/2026</FilingDate>
                    <DocID>10000001</DocID>
                  </Member>
                </FinancialDisclosure>
            """,
        }
    )

    filings = parse_house_ptr_zip(content, year=2026)

    assert [filing.doc_id for filing in filings] == ["20034201"]
    assert filings[0].name == "Hon. Mark Alford"
    assert filings[0].state == "MO04"
    assert filings[0].filing_date == "3/31/2026"


def test_house_ptr_item_contains_pdf_url_and_trade_rows() -> None:
    filing = HousePtrFiling(
        doc_id="20024228",
        year=2026,
        filing_type="P",
        name="Jane Doe",
        status="Member",
        state="CA",
        filing_date="2026-06-30",
        raw={"DocID": "20024228"},
    )
    tables = [[["Asset", "Transaction Type", "Transaction Date", "Amount"], ["AAPL", "Purchase", "2026-06-20", "$1,001 - $15,000"]]]
    trade_rows = normalize_house_ptr_tables(tables)

    item = house_ptr_raw_item(
        filing,
        pdf_url="https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20024228.pdf",
        tables=tables,
        trade_rows=trade_rows,
        extraction_status="succeeded",
        extraction_error=None,
    )

    assert item.source_id == "20024228"
    assert item.source_type == "house_ptr_disclosures"
    assert item.metadata["pdf_url"].endswith("/2026/20024228.pdf")
    assert item.metadata["trade_rows"][0]["fields"]["asset"] == "AAPL"


async def test_house_ptr_collector_uses_repository_existing_doc_ids() -> None:
    collector = HousePtrDisclosureCollector(
        collector_id="house.ptr",
        collector_config=CollectorConfig(kind="house_ptr_disclosures", year=2026),
    )
    repository = FakeHouseRepository({"20024228"})

    collector.set_repository(repository)

    assert await collector._existing_doc_ids(year=2026) == {"20024228"}


def test_normalize_house_ptr_tables_extracts_display_fields() -> None:
    rows = normalize_house_ptr_tables(
        [[["Asset", "Transaction Type", "Transaction Date", "Amount"], ["MSFT", "Sale", "2026-06-01", "$15,001 - $50,000"]]]
    )

    assert rows[0]["fields"] == {
        "asset": "MSFT",
        "asset_type_code": None,
        "asset_type_label": None,
        "stock_ticker": None,
        "transaction_type": "Sale",
        "transaction_date": "2026-06-01",
        "amount": "$15,001 - $50,000",
    }


def test_parse_house_asset_metadata_extracts_stock_type_and_ticker() -> None:
    metadata = parse_house_asset_metadata("Amazon.com, Inc. - Common Stock (AMZN) [ST]")

    assert metadata == {
        "asset_type_code": "ST",
        "asset_type_label": "Stocks, including ADRs",
        "stock_ticker": "AMZN",
    }
    assert parse_house_asset_metadata("AT&T Inc. Depositary Shares (T$A) [ST]")["stock_ticker"] == "T$A"
    assert parse_house_asset_metadata("Berkshire Hathaway Inc. New Common Stock (BRK.B) [ST]")["stock_ticker"] == "BRK.B"


def test_parse_house_asset_metadata_preserves_non_stock_and_unknown_codes() -> None:
    assert parse_house_asset_metadata("US Treasury Note 3.5% DUE 01/31/28 (91282CGH8) [GS]") == {
        "asset_type_code": "GS",
        "asset_type_label": "Government Securities and Agency Debt",
        "stock_ticker": None,
    }
    assert parse_house_asset_metadata("Microsoft Corporation - Common Stock (MSFT) [OP]") == {
        "asset_type_code": "OP",
        "asset_type_label": "Options",
        "stock_ticker": None,
    }
    assert parse_house_asset_metadata("SBA Communications Corporation -") == {
        "asset_type_code": None,
        "asset_type_label": None,
        "stock_ticker": None,
    }


def test_normalize_house_ptr_tables_recovers_collapsed_pdf_rows() -> None:
    rows = normalize_house_ptr_tables(
        [
            [
                ["Owner", "Asset", "Transaction Type", "Transaction Date", "Notification Date", "Amount"],
                [
                    "SP American Water Works Company, S 01/14/2026 02/04/2026 $50,001 - Inc. Common Stock (AWK) [ST] $100,000 F S : New",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
                [
                    "Ferguson Enterprises Inc. Common P 12/12/2025 01/06/2026 $15,001 - Stock (FERG) [ST] $50,000 F S : New",
                    "",
                    "",
                    "",
                    "",
                    "",
                ],
            ]
        ]
    )

    assert rows[0]["fields"] == {
        "asset": "American Water Works Company, Inc. Common Stock (AWK) [ST]",
        "asset_type_code": "ST",
        "asset_type_label": "Stocks, including ADRs",
        "stock_ticker": "AWK",
        "transaction_type": "S",
        "transaction_date": "01/14/2026",
        "amount": "$50,001 - $100,000",
    }
    assert rows[1]["fields"] == {
        "asset": "Ferguson Enterprises Inc. Common Stock (FERG) [ST]",
        "asset_type_code": "ST",
        "asset_type_label": "Stocks, including ADRs",
        "stock_ticker": "FERG",
        "transaction_type": "P",
        "transaction_date": "12/12/2025",
        "amount": "$15,001 - $50,000",
    }


class FakeHouseRepository:
    def __init__(self, doc_ids: set[str]) -> None:
        self.doc_ids = doc_ids

    async def existing_house_ptr_doc_ids(self, *, year: int | None = None) -> set[str]:
        return self.doc_ids


def _zip_bytes(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content.strip())
    return buffer.getvalue()
