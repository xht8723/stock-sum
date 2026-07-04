"""SEC 13F collector tests."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from stock_sum.collectors.api.sec_13f import discover_latest_13f_dataset, parse_sec_13f_zip


def test_discover_latest_13f_dataset_uses_first_zip_link() -> None:
    html = """
    <h2>Data Downloads</h2>
    <a href="/files/structureddata/data/form-13f-data-sets/2026q1_form13f.zip">2026 March April May 13F</a>
    <a href="/older.zip">Older 13F</a>
    """

    latest = discover_latest_13f_dataset(html, base_url="https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets")

    assert latest.label == "2026 March April May 13F"
    assert latest.url == "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/2026q1_form13f.zip"


def test_parse_sec_13f_zip_reads_documented_tsv_files() -> None:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("SUBMISSION.tsv", "ACCESSION_NUMBER\tFILING_DATE\tSUBMISSIONTYPE\tCIK\tPERIODOFREPORT\n0001\t31-MAY-2026\t13F-HR\t123\t31-MAR-2026\n")
        archive.writestr("COVERPAGE.tsv", "ACCESSION_NUMBER\tFILINGMANAGER_NAME\tREPORTTYPE\n0001\tBerkshire Hathaway Inc\t13F HOLDINGS REPORT\n")
        archive.writestr("INFOTABLE.tsv", "ACCESSION_NUMBER\tINFOTABLE_SK\tNAMEOFISSUER\tCUSIP\tVALUE\tSSHPRNAMT\n0001\t1\tNVIDIA CORP\t67066G104\t1000\t50\n")

    rows = parse_sec_13f_zip(buffer.getvalue())

    assert rows["submissions"][0]["ACCESSION_NUMBER"] == "0001"
    assert rows["coverpages"][0]["FILINGMANAGER_NAME"] == "Berkshire Hathaway Inc"
    assert rows["info_tables"][0]["NAMEOFISSUER"] == "NVIDIA CORP"
