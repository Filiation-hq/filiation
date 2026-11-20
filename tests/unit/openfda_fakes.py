"""Offline stand-ins for openFDA's download index and export zips, shared by ingestion tests."""

import io
import json
import zipfile
from pathlib import Path

from filiation.pipeline.crawler.openfda_bulk import DOWNLOAD_INDEX_URL

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


def export_body(
    records: list[dict],
    *,
    last_updated: str = "2026-11-09",
    skip: int = 0,
    total: int | None = None,
) -> str:
    """Serializes records in openFDA's export layout with the meta.results block real partitions carry.

    Args:
        records: Records for the "results" array; their count becomes meta.results.limit.
        last_updated: Value for meta.last_updated.
        skip: Offset of this partition's first record within the whole export.
        total: Whole-export record count; defaults to len(records) for a single partition.

    Returns:
        str: The export JSON document.
    """
    results = {"skip": skip, "limit": len(records), "total": len(records) if total is None else total}
    return json.dumps({"meta": {"last_updated": last_updated, "results": results}, "results": records})


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
    body = export_body([{"product_res_number": "Z-0001-2026", "res_event_number": "90001"}, {"product_res_number": "Z-0002-2026", "res_event_number": "90001"}])
    return make_export_zip({"device-recall-0001-of-0001.json": body})


def enforcement_export_zip(records: list[dict] | None = None) -> bytes:
    """Builds an enforcement export zip matching the index fixture's enforcement entry.

    Args:
        records: Enforcement records to include; defaults to ENFORCEMENT_RECORDS.

    Returns:
        bytes: Zip bytes for device-enforcement-0001-of-0001.json.zip.
    """
    body = export_body(ENFORCEMENT_RECORDS if records is None else records)
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


PART_URL = "https://download.open.fda.gov/device/510k/device-510k-{n:04d}-of-0002.json.zip"


def records(start: int, count: int) -> list[dict]:
    """Builds distinct 510(k) records numbered from start.

    Args:
        start: Number of the first record.
        count: How many records to build.

    Returns:
        list[dict]: Records with unique k_number values.
    """
    return [{"k_number": f"K{start + i:06d}"} for i in range(count)]


def opener_serving(parts: list[tuple[str, str, int]], total_records: int | None | str = "sum") -> FakeOpener:
    """Serves a 510(k) index listing the given partitions, and each partition's body as a zip.

    Args:
        parts: (url, export JSON body, records the index lists) per partition, in index order.
        total_records: Index total_records; "sum" uses the sum of listed records, None omits it.

    Returns:
        FakeOpener: Opener for the index and every listed partition URL.
    """
    index = load_index()
    entry = index["results"]["device"]["510k"]
    entry["partitions"] = [{"file": url, "records": listed} for url, _, listed in parts]
    if total_records == "sum":
        entry["total_records"] = sum(listed for _, _, listed in parts)
    elif total_records is None:
        entry.pop("total_records", None)
    else:
        entry["total_records"] = total_records
    responses = {DOWNLOAD_INDEX_URL: json.dumps(index).encode("utf-8")}
    for url, body, _ in parts:
        responses[url] = make_export_zip({url.rsplit("/", 1)[-1].removesuffix(".zip"): body})
    return FakeOpener(responses)
