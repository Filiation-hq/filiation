"""Offline stand-ins for openFDA's download index and export zips, shared by ingestion tests."""

import io
import json
import zipfile
from pathlib import Path

from recension.pipeline.crawler.openfda_bulk import DOWNLOAD_INDEX_URL

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "openfda"
PARTITION_URL = "https://download.open.fda.gov/device/510k/device-510k-0001-of-0001.json.zip"
RECALL_URL = "https://download.open.fda.gov/device/recall/device-recall-0001-of-0001.json.zip"
ENFORCEMENT_URL = "https://download.open.fda.gov/device/enforcement/device-enforcement-0001-of-0001.json.zip"
# Matches the two recall fixture records: one classified, one not yet (real 2026-11-16 export has 1).
ENFORCEMENT_RECORDS = [
    {"recall_number": "Z-0001-2026", "event_id": "90001", "classification": "Class II", "product_type": "Devices"},
    {"recall_number": "Z-0002-2026", "event_id": "90001", "classification": "Not Yet Classified", "product_type": "Devices"},
]
FIXED_ZIP_TIME = (2026, 11, 9, 0, 0, 0)
KEY ="openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip"


def load_index() -> dict:
    """Loads the trimmed download.json fixture.

    Returns:
        dict: Parsed index with device/510k (3 records), device/recall (2), and device/enforcement (2).
    """
    return json.loads((FIXTURES / "download.json").read_text(encoding="utf-8"))


def make_export_zip(entries: dict[str, bytes | str]) -> bytes:
    """Builds an in-memory zip with the given entries, like openFDA's partition zips, byte-identical across calls.

    Args:
        entries: Mapping of entry name to its uncompressed content.

    Returns:
        bytes: The zip archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            # A fixed timestamp keeps the bytes, and so the SHA-256, independent of the clock.
            archive.writestr(zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME), data, zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def fixture_export_zip() -> bytes:
    """Zips the 3-record 510(k) export fixture under openFDA's inner file name.

    Returns:
        bytes: Zip bytes for device-510k-0001-of-0001.json.zip.
    """
    body = (FIXTURES / "device-510k-0001-of-0001.json").read_bytes()
    return make_export_zip({"device-510k-0001-of-0001.json": body})


def recall_export_zip() -> bytes:
    """Builds a 2-record recall export zip matching the index fixture's recall entry.

    Returns:
        bytes: Zip bytes for device-recall-0001-of-0001.json.zip.
    """
    body = json.dumps({"meta": {}, "results": [{"product_res_number": "Z-0001-2026", "res_event_number": "90001"}, {"product_res_number": "Z-0002-2026", "res_event_number": "90001"}]})
    return make_export_zip({"device-recall-0001-of-0001.json": body})


def enforcement_export_zip(records: list[dict] | None = None) -> bytes:
    """Builds an enforcement export zip matching the index fixture's enforcement entry.

    Args:
        records: Enforcement records to include; defaults to ENFORCEMENT_RECORDS.

    Returns:
        bytes: Zip bytes for device-enforcement-0001-of-0001.json.zip.
    """
    body = json.dumps({"meta": {}, "results": ENFORCEMENT_RECORDS if records is None else records})
    return make_export_zip({"device-enforcement-0001-of-0001.json": body})


class FakeOpener:
    """Serves fixed bytes per URL and records every URL requested."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        """Stores the URL-to-bytes map.

        Args:
            responses: Bytes to return for each URL; any other URL raises KeyError.
        """
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> io.BytesIO:
        """Returns a readable stream for url.

        Args:
            url: Requested URL.

        Returns:
            io.BytesIO: Stream over the configured bytes.
        """
        self.calls.append(url)
        return io.BytesIO(self.responses[url])


def opener_for(zip_bytes: bytes, index: dict | None = None, enforcement: bytes | None = None) -> FakeOpener:
    """Builds a FakeOpener serving the index, a 510(k) partition zip, the recall zip, and the enforcement zip.

    Args:
        zip_bytes: Bytes served at PARTITION_URL.
        index: Index to serve; defaults to the download.json fixture.
        enforcement: Bytes served at ENFORCEMENT_URL; defaults to enforcement_export_zip().

    Returns:
        FakeOpener: Opener for DOWNLOAD_INDEX_URL, PARTITION_URL, RECALL_URL, and ENFORCEMENT_URL only.
    """
    index_bytes = json.dumps(index or load_index()).encode("utf-8")
    return FakeOpener(
        {
            DOWNLOAD_INDEX_URL: index_bytes,
            PARTITION_URL: zip_bytes,
            RECALL_URL: recall_export_zip(),
            ENFORCEMENT_URL: enforcement or enforcement_export_zip(),
        }
    )
