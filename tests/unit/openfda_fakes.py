"""Offline stand-ins for openFDA's download index and export zips, shared by ingestion tests."""

import io
import json
import zipfile
from pathlib import Path

from recension.pipeline.crawler.openfda_bulk import DOWNLOAD_INDEX_URL

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "openfda"
PARTITION_URL = "https://download.open.fda.gov/device/510k/device-510k-0001-of-0001.json.zip"
RECALL_URL = "https://download.open.fda.gov/device/recall/device-recall-0001-of-0001.json.zip"
KEY = "openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip"


def load_index() -> dict:
    """Loads the trimmed download.json fixture.

    Returns:
        dict: Parsed index with device/510k (3 records) and device/recall (2 records).
    """
    return json.loads((FIXTURES / "download.json").read_text(encoding="utf-8"))


def make_export_zip(entries: dict[str, bytes | str]) -> bytes:
    """Builds an in-memory zip with the given entries, like openFDA's partition zips.

    Args:
        entries: Mapping of entry name to its uncompressed content.

    Returns:
        bytes: The zip archive.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
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
    body = json.dumps({"meta": {}, "results": [{"recall_number": "Z-0001-2026"}, {"recall_number": "Z-0002-2026"}]})
    return make_export_zip({"device-recall-0001-of-0001.json": body})


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


def opener_for(zip_bytes: bytes, index: dict | None = None) -> FakeOpener:
    """Builds a FakeOpener serving the index, a 510(k) partition zip, and the recall zip.

    Args:
        zip_bytes: Bytes served at PARTITION_URL.
        index: Index to serve; defaults to the download.json fixture.

    Returns:
        FakeOpener: Opener for DOWNLOAD_INDEX_URL, PARTITION_URL, and RECALL_URL only.
    """
    index_bytes = json.dumps(index or load_index()).encode("utf-8")
    return FakeOpener(
        {DOWNLOAD_INDEX_URL: index_bytes, PARTITION_URL: zip_bytes, RECALL_URL: recall_export_zip()}
    )
