"""Unit tests for the openFDA bulk-export ingester in stemma.pipeline.crawler.openfda_bulk (#39).

Task map:
    Input: openFDA's download index (download.json) and the export zip it points to, served
        by a fake opener; a RawStore to write into.
    Output: each partition zip stored byte-for-byte at
        openfda/<source>/export_date=<YYYY-MM-DD>/<file name>, with its SHA-256, size, and
        record count returned for the run manifest (#42).
    Edge cases: unknown source; malformed export_date (it becomes part of the key); record
        strings containing braces, brackets, and escaped quotes; a zip with more than one
        entry; a record count that disagrees with the index; a rerun of the same export
        (unchanged, nothing written); the same export_date re-published with different bytes.
    Invariants: partition URLs come only from the index; nothing is written unless the record
        count matches; an existing object is never replaced; no network in tests. The real
        510(k) export inflates to ~1.6 GB, so counting must stream, never json.load the file.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from stemma.core.storage.raw_store import LocalRawStore
from stemma.pipeline.crawler.openfda_bulk import (
    DOWNLOAD_INDEX_URL,
    ExportConflictError,
    ExportIntegrityError,
    count_export_records,
    ingest_export,
    parse_export_listing,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "openfda"
PARTITION_URL = "https://download.open.fda.gov/device/510k/device-510k-0001-of-0001.json.zip"
KEY = "openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip"


def load_index() -> dict:
    """Loads the trimmed download.json fixture.

    Returns:
        dict: Parsed index with device/510k (3 records) and device/recall (2 records).
    """
    return json.loads((FIXTURES / "download.json").read_text(encoding="utf-8"))


def make_export_zip(entries: dict[str, bytes]) -> bytes:
    """Builds an in-memory zip with the given entries, like openFDA's partition zips.

    Args:
        entries: Mapping of entry name to its uncompressed bytes.

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
    """Builds a FakeOpener serving the index and one 510(k) partition zip.

    Args:
        zip_bytes: Bytes served at PARTITION_URL.
        index: Index to serve; defaults to the download.json fixture.

    Returns:
        FakeOpener: Opener for DOWNLOAD_INDEX_URL and PARTITION_URL only.
    """
    index_bytes = json.dumps(index or load_index()).encode("utf-8")
    return FakeOpener({DOWNLOAD_INDEX_URL: index_bytes, PARTITION_URL: zip_bytes})


def test_parse_export_listing_reads_export_date_and_partitions_for_510k():
    # Sample: download.json fixture; device/510k has export_date 2026-11-09 and one
    # 3-record partition, mirroring the real index fetched 2026-11-12.
    listing = parse_export_listing(load_index(), "510k")
    assert listing.export_date == "2026-11-09"
    assert [(p.url, p.records, p.file_name) for p in listing.partitions] == [
        (PARTITION_URL, 3, "device-510k-0001-of-0001.json.zip")
    ]


def test_parse_export_listing_rejects_unknown_source():
    # Sample: "pma" is a real openFDA device dataset, but not one Stemma ingests.
    with pytest.raises(ValueError):
        parse_export_listing(load_index(), "pma")


def test_parse_export_listing_rejects_non_iso_export_date():
    # Sample: export_date "11/09/2026". It becomes part of the storage key, so anything
    # other than YYYY-MM-DD would break the export_date=<date> partition layout.
    index = load_index()
    index["results"]["device"]["510k"]["export_date"] = "11/09/2026"
    with pytest.raises(ValueError):
        parse_export_listing(index, "510k")


def test_count_export_records_counts_records_despite_braces_and_quotes_in_strings(tmp_path):
    # Sample: the 3-record fixture. One device_name holds "{" and "]", one applicant holds
    # escaped quotes; a naive brace count would get these wrong.
    path = tmp_path / "export.zip"
    path.write_bytes(fixture_export_zip())
    assert count_export_records(path) == 3


def test_count_export_records_rejects_zip_with_more_than_one_entry(tmp_path):
    # Sample: a zip holding two JSON files. openFDA ships one file per partition, so a
    # second entry means the format changed and the count can't be trusted.
    path = tmp_path / "export.zip"
    path.write_bytes(make_export_zip({"a.json": b'{"results": []}', "b.json": b"{}"}))
    with pytest.raises(ExportIntegrityError):
        count_export_records(path)


def test_ingest_export_stores_zip_byte_for_byte_and_reports_sha256_and_count(tmp_path):
    # Sample: fixture index + 3-record zip into an empty local store.
    # Expect the exact zip bytes at KEY, plus its SHA-256, size, and record count.
    zip_bytes = fixture_export_zip()
    store = LocalRawStore(tmp_path / "raw")
    result = ingest_export("510k", store, opener=opener_for(zip_bytes))

    assert result.export_date == "2026-11-09"
    [part] = result.partitions
    assert (part.key, part.status, part.records) == (KEY, "stored", 3)
    assert part.sha256 == hashlib.sha256(zip_bytes).hexdigest()
    assert part.size_bytes == len(zip_bytes)
    assert (tmp_path / "raw" / KEY).read_bytes() == zip_bytes


def test_ingest_export_takes_partition_urls_from_the_download_index(tmp_path):
    # Sample: fixture index served by the fake opener, which refuses any other URL.
    # The index must be requested first, then exactly the partition URL it lists.
    opener = opener_for(fixture_export_zip())
    ingest_export("510k", LocalRawStore(tmp_path), opener=opener)
    assert opener.calls == [DOWNLOAD_INDEX_URL, PARTITION_URL]


def test_ingest_export_raises_and_writes_nothing_when_record_count_disagrees(tmp_path):
    # Sample: index claims 4 records; the zip holds 3. A truncated or changed export must
    # never become a raw snapshot.
    index = load_index()
    index["results"]["device"]["510k"]["partitions"][0]["records"] = 4
    store = LocalRawStore(tmp_path / "raw")
    with pytest.raises(ExportIntegrityError):
        ingest_export("510k", store, opener=opener_for(fixture_export_zip(), index))
    assert store.list() == []


def test_ingest_export_marks_rerun_of_same_export_unchanged_without_writing(tmp_path):
    # Sample: the same 2026-11-09 export ingested twice (two weekly runs with no new
    # openFDA export). The second run reports "unchanged" and the store holds one object.
    zip_bytes = fixture_export_zip()
    store = LocalRawStore(tmp_path / "raw")
    ingest_export("510k", store, opener=opener_for(zip_bytes))
    second = ingest_export("510k", store, opener=opener_for(zip_bytes))

    assert [p.status for p in second.partitions] == ["unchanged"]
    assert second.partitions[0].sha256 == hashlib.sha256(zip_bytes).hexdigest()
    assert store.list() == [KEY]


def test_ingest_export_raises_conflict_when_same_export_date_has_different_bytes(tmp_path):
    # Sample: 2026-11-09 stored, then openFDA re-publishes 2026-11-09 with one record's
    # applicant edited. Write-once forbids replacing it, so the run must fail loudly.
    store = LocalRawStore(tmp_path / "raw")
    ingest_export("510k", store, opener=opener_for(fixture_export_zip()))
    body = (FIXTURES / "device-510k-0001-of-0001.json").read_text(encoding="utf-8")
    edited = make_export_zip(
        {"device-510k-0001-of-0001.json": body.replace("Legacy Devices Co", "Legacy Devices LLC")}
    )
    with pytest.raises(ExportConflictError):
        ingest_export("510k", store, opener=opener_for(edited))
    assert (tmp_path / "raw" / KEY).read_bytes() == fixture_export_zip()


def test_ingest_openfda_cli_stores_export_and_prints_result_json(tmp_path, monkeypatch):
    # Sample: `stemma-pipeline ingest-openfda --source 510k` with the store and opener
    # patched to a temp LocalRawStore and the fixture opener. Expect exit 0 and a JSON
    # summary naming the stored key.
    import stemma.pipeline.cli as cli_module

    store = LocalRawStore(tmp_path / "raw")
    monkeypatch.setattr(cli_module, "raw_store_from_env", lambda: store)
    monkeypatch.setattr(cli_module, "default_opener", opener_for(fixture_export_zip()))

    result = CliRunner().invoke(cli_module.cli, ["ingest-openfda", "--source", "510k"])

    assert result.exit_code == 0, result.output
    summary = json.loads(result.output)
    assert summary["partitions"][0]["key"] == KEY
    assert summary["partitions"][0]["status"] == "stored"

