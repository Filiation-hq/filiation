"""Unit tests for the openFDA bulk-export ingester in recension.pipeline.crawler.openfda_bulk (#39).

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

iter_export_records (#40) streams the same files record by record for the weekly diff; it
shares the counter's parser, so the same malformed inputs fail it the same way.
"""

import hashlib

import pytest
from openfda_fakes import (
    FIXTURES,
    KEY,
    PARTITION_URL,
    fixture_export_zip,
    load_index,
    make_export_zip,
    opener_for,
)
from recension.core.storage.raw_store import LocalRawStore
from recension.pipeline.crawler.openfda_bulk import (
    DOWNLOAD_INDEX_URL,
    ExportConflictError,
    ExportIntegrityError,
    count_export_records,
    ingest_export,
    iter_export_records,
    parse_export_listing,
    read_export_stats,
)


def test_parse_export_listing_reads_export_date_and_partitions_for_510k():
    # Sample: download.json fixture; device/510k has export_date 2026-11-09 and one
    # 3-record partition, mirroring the real index fetched 2026-11-12.
    listing = parse_export_listing(load_index(), "510k")
    assert listing.export_date == "2026-11-09"
    assert [(p.url, p.records, p.file_name) for p in listing.partitions] == [
        (PARTITION_URL, 3, "device-510k-0001-of-0001.json.zip")
    ]


def test_parse_export_listing_rejects_unknown_source():
    # Sample: "pma" is a real openFDA device dataset, but not one Recension ingests.
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



def test_read_export_stats_returns_count_and_openfda_last_updated(tmp_path):
    # Sample: the 3-record fixture, whose meta.last_updated is 2026-11-09. ADR-008 logs
    # last_updated on every run to measure openFDA's real refresh cadence.
    path = tmp_path / "export.zip"
    path.write_bytes(fixture_export_zip())
    stats = read_export_stats(path)
    assert (stats.records, stats.last_updated) == (3, "2026-11-09")


def test_ingest_export_reports_last_updated_per_partition(tmp_path):
    # Sample: fixture index + 3-record zip. The partition result carries meta.last_updated.
    result = ingest_export("510k", LocalRawStore(tmp_path), opener=opener_for(fixture_export_zip()))
    assert result.partitions[0].last_updated == "2026-11-09"


def test_parse_export_listing_rejects_a_source_with_no_partitions():
    # Sample: download.json listing device/510k with an empty partitions list. Treating
    # that as "nothing changed" would publish a manifest with no 510(k) data at all.
    index = load_index()
    index["results"]["device"]["510k"]["partitions"] = []
    with pytest.raises(ValueError, match="no partitions"):
        parse_export_listing(index, "510k")


def test_parse_export_listing_rejects_a_partition_with_zero_records():
    # Sample: one partition claiming 0 records. The real 510(k) export has 176,625; an
    # empty export means openFDA published something broken.
    index = load_index()
    index["results"]["device"]["510k"]["partitions"][0]["records"] = 0
    with pytest.raises(ValueError, match="0 records"):
        parse_export_listing(index, "510k")


def test_parse_export_listing_names_the_source_missing_from_the_index():
    # Sample: download.json with device/510k removed (openFDA renamed or dropped it).
    # Expect a ValueError naming the source, not a bare KeyError.
    index = load_index()
    del index["results"]["device"]["510k"]
    with pytest.raises(ValueError, match="510k"):
        parse_export_listing(index, "510k")


def test_count_export_records_reports_a_corrupt_zip_as_an_integrity_error(tmp_path):
    # Sample: a download cut off after 20 bytes. Expect ExportIntegrityError, the same
    # error as every other bad export, not zipfile.BadZipFile.
    path = tmp_path / "export.zip"
    path.write_bytes(fixture_export_zip()[:20])
    with pytest.raises(ExportIntegrityError):
        count_export_records(path)


def test_iter_export_records_yields_each_record_in_file_order(tmp_path):
    # Sample: the 3-record fixture (K210758, K030123, K882211), including a device_name
    # with "{" and "]" and an applicant with escaped quotes. Records must come back whole.
    path = tmp_path / "export.zip"
    path.write_bytes(fixture_export_zip())
    records = list(iter_export_records(path))
    assert [r["k_number"] for r in records] == ["K210758", "K030123", "K882211"]
    assert records[1]["applicant"] == 'Example "Quoted" Medical, Inc.'


def test_iter_export_records_rejects_an_export_with_no_results_array(tmp_path):
    # Sample: {"meta": {}} with no "results" key. Yielding nothing would make every record
    # look removed in the weekly diff, so it must fail like the counter does.
    path = tmp_path / "export.zip"
    path.write_bytes(make_export_zip({"x.json": b'{"meta": {}}'}))
    with pytest.raises(ExportIntegrityError):
        list(iter_export_records(path))
