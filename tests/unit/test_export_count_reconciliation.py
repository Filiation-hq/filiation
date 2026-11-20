"""Unit tests for reconciling every count openFDA states about an export before it is stored (#46).

Task map:
    Input: download.json (per-partition "records" and per-source "total_records") and each
        partition zip, whose meta.results carries {skip, limit, total}. Verified against the
        live index on 2026-11-19: total is the whole export's count and repeats in every
        partition; limit is that partition's count (device/udi 0052-of-0052 has limit 82,695,
        not the 100,000 page size); skip is the partition's offset; total_records equals the
        sum of partition records for every source in the index.
    Output: ingest_export stores the export only when every stated count agrees with the
        records actually streamed; otherwise ExportIntegrityError (zip contents) or ValueError
        (index contents), with nothing written.
    Tolerance: zero. The index and the zips come from the same export job, so any difference
        means a truncated, mixed, or re-published download. The live API's meta.results.total
        is not compared: on 2026-11-19 it reported 176,683 510(k) records under
        last_updated 2026-11-09, while the stored 2026-11-09 export holds 176,625, so the API
        and the export legitimately disagree.
    Edge cases: meta missing, null, or without "results"; a count that is a string, bool,
        float, null, or negative; limit disagreeing with the streamed count; total disagreeing
        with the streamed sum; partitions disagreeing on total (two export builds mixed); the
        index omitting a partition; the same partition listed twice (overlapping skips); a gap
        between skips; partitions listed out of skip order; total_records disagreeing,
        missing, or malformed; "meta" or "results" appearing twice at the top level; meta
        placed after results.
    Invariants: a failed reconciliation writes nothing; reading already-stored snapshots stays
        lenient (only ingestion enforces meta.results), so old raw objects remain readable.
"""

import json

import pytest
from filiation.core.storage.raw_store import LocalRawStore
from filiation.pipeline.crawler.openfda_bulk import (
    ExportIntegrityError,
    ingest_export,
    parse_export_listing,
    read_export_stats,
)
from openfda_fakes import (
    PART_URL,
    PARTITION_URL,
    FakeOpener,
    export_body,
    load_index,
    make_export_zip,
    opener_serving,
    records,
)


def single(body: str, listed: int = 3) -> FakeOpener:
    """Serves the one-partition 510(k) export with a custom body.

    Args:
        body: Export JSON for the partition.
        listed: Records the index lists for it.

    Returns:
        FakeOpener: Opener for the index and PARTITION_URL.
    """
    return opener_serving([(PARTITION_URL, body, listed)])


def meta_body(meta: object, count: int = 3) -> str:
    """Builds an export body with an arbitrary meta value and count records.

    Args:
        meta: Value for the top-level "meta" key.
        count: Number of records.

    Returns:
        str: The export JSON document.
    """
    return json.dumps({"meta": meta, "results": records(1, count)})


def assert_rejected(opener: FakeOpener, tmp_path, match: str) -> None:
    """Asserts ingest_export raises ExportIntegrityError matching match and stores nothing.

    Args:
        opener: Opener serving the export under test.
        tmp_path: pytest temp dir for the store.
        match: Regex the error message must contain.
    """
    store = LocalRawStore(tmp_path / "raw")
    with pytest.raises(ExportIntegrityError, match=match):
        ingest_export("510k", store, opener=opener)
    assert store.list() == []


def test_ingest_export_rejects_a_partition_whose_meta_limit_differs_from_its_streamed_count(tmp_path):
    # Sample: 3 records, index lists 3, meta.results says limit 2 but total 3. Only the
    # per-partition limit is wrong, so the error must name limit, and nothing is stored.
    body = json.dumps({"meta": {"results": {"skip": 0, "limit": 2, "total": 3}}, "results": records(1, 3)})
    assert_rejected(single(body), tmp_path, "limit")


def test_ingest_export_rejects_an_export_whose_meta_total_differs_from_the_streamed_sum(tmp_path):
    # Sample: one 3-record partition (index lists 3) whose meta.results.total says 4, as if
    # the source had a 4th record the file lost. Count against index alone would pass.
    assert_rejected(single(export_body(records(1, 3), total=4)), tmp_path, "total")


def test_ingest_export_rejects_partitions_that_disagree_on_the_export_total(tmp_path):
    # Sample: partition 1 (2 records, total 4) from one export build and partition 2
    # (2 records, total 5) from another, as when a CDN serves one stale partition mid-publish.
    parts = [
        (PART_URL.format(n=1), export_body(records(1, 2), skip=0, total=4), 2),
        (PART_URL.format(n=2), export_body(records(3, 2), skip=2, total=5), 2),
    ]
    assert_rejected(opener_serving(parts), tmp_path, "disagree")


def test_ingest_export_rejects_an_index_that_omits_a_partition(tmp_path):
    # Sample: the index lists only partition 1 of 2 (2 records); its meta says the export
    # totals 4. Every per-partition count matches, so only the total exposes the loss.
    parts = [(PART_URL.format(n=1), export_body(records(1, 2), skip=0, total=4), 2)]
    assert_rejected(opener_serving(parts), tmp_path, "total")


def test_ingest_export_rejects_the_same_partition_listed_twice(tmp_path):
    # Sample: two 2-record partitions both at skip 0 with total 4. Counts sum to 4, so only
    # the overlapping skips show that one partition was served twice and one never came.
    parts = [
        (PART_URL.format(n=1), export_body(records(1, 2), skip=0, total=4), 2),
        (PART_URL.format(n=2), export_body(records(1, 2), skip=0, total=4), 2),
    ]
    assert_rejected(opener_serving(parts), tmp_path, "skip")


def test_ingest_export_rejects_a_gap_between_partition_skips(tmp_path):
    # Sample: partitions at skip 0 (2 records) and skip 3 (2 records), total 4. Sums agree,
    # but records 2..3 are unaccounted for and record 5 lies past the total.
    parts = [
        (PART_URL.format(n=1), export_body(records(1, 2), skip=0, total=4), 2),
        (PART_URL.format(n=2), export_body(records(3, 2), skip=3, total=4), 2),
    ]
    assert_rejected(opener_serving(parts), tmp_path, "skip")


def test_ingest_export_accepts_partitions_listed_out_of_skip_order(tmp_path):
    # Sample: a valid 2-partition export (skips 0 and 2, total 4) whose index lists
    # partition 2 first. Order in the index is not a defect; reconciliation sorts by skip.
    parts = [
        (PART_URL.format(n=2), export_body(records(3, 2), skip=2, total=4), 2),
        (PART_URL.format(n=1), export_body(records(1, 2), skip=0, total=4), 2),
    ]
    result = ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_serving(parts))
    assert [p.records for p in result.partitions] == [2, 2]


def test_ingest_export_rejects_an_export_with_no_meta(tmp_path):
    # Sample: {"results": [3 records]} with no meta key. An upstream format change must stop
    # the run, not silently skip the total check.
    assert_rejected(single(json.dumps({"results": records(1, 3)})), tmp_path, "meta.results")


def test_ingest_export_rejects_an_export_whose_meta_is_null(tmp_path):
    # Sample: "meta": null with 3 records.
    assert_rejected(single(meta_body(None)), tmp_path, "meta.results")


def test_ingest_export_rejects_an_export_whose_meta_has_no_results_block(tmp_path):
    # Sample: meta holds last_updated only, the shape the old test fakes used.
    assert_rejected(single(meta_body({"last_updated": "2026-11-09"})), tmp_path, "meta.results")


@pytest.mark.parametrize(
    "bad",
    ["3", True, 3.0, None, -3],
    ids=["string", "bool", "float", "null", "negative"],
)
def test_ingest_export_rejects_a_meta_count_that_is_not_a_non_negative_integer(tmp_path, bad):
    # Sample: 3 records with meta.results.total set to "3", True, 3.0, null, or -3. A string
    # or float that happens to equal the count, or a bool (an int subclass in Python), must
    # not pass as a count.
    body = meta_body({"results": {"skip": 0, "limit": 3, "total": bad}})
    assert_rejected(single(body), tmp_path, "meta.results")


def test_parse_export_listing_rejects_total_records_that_disagree_with_partition_records():
    # Sample: index lists one 3-record partition but total_records 4.
    index = load_index()
    index["results"]["device"]["510k"]["total_records"] = 4
    with pytest.raises(ValueError, match="total_records"):
        parse_export_listing(index, "510k")


@pytest.mark.parametrize("bad", ["3", True, -3], ids=["string", "bool", "negative"])
def test_parse_export_listing_rejects_malformed_total_records(bad):
    # Sample: total_records of "3", True, or -3 alongside a 3-record partition.
    index = load_index()
    index["results"]["device"]["510k"]["total_records"] = bad
    with pytest.raises(ValueError, match="total_records"):
        parse_export_listing(index, "510k")


def test_parse_export_listing_records_total_records_as_none_when_the_index_omits_it():
    # Sample: index entry without total_records. The zips' meta still carries the export
    # total, so a missing index field is tolerated and reported as None, not guessed.
    index = load_index()
    del index["results"]["device"]["510k"]["total_records"]
    assert parse_export_listing(index, "510k").total_records is None


def test_parse_export_listing_reads_total_records():
    # Sample: the fixture index lists total_records 3 for 510(k).
    assert parse_export_listing(load_index(), "510k").total_records == 3


def test_read_export_stats_rejects_an_export_with_two_meta_objects(tmp_path):
    # Sample: {"meta": {total 3}, "results": [...3], "meta": {}}. JSON allows duplicate
    # keys; which total is meant is ambiguous, so it must not silently keep the last one.
    meta = '"meta": {"results": {"skip": 0, "limit": 3, "total": 3}}'
    body = "{" + meta + ', "results": ' + json.dumps(records(1, 3)) + ', "meta": {}}'
    path = tmp_path / "x.zip"
    path.write_bytes(make_export_zip({"x.json": body}))
    with pytest.raises(ExportIntegrityError, match="meta"):
        read_export_stats(path)


def test_read_export_stats_rejects_an_export_with_two_results_arrays(tmp_path):
    # Sample: two top-level "results" arrays of 3 records each. Counting both would report 6
    # records for a file openFDA built as 3.
    body = '{"meta": {}, "results": ' + json.dumps(records(1, 3)) + ', "results": ' + json.dumps(records(4, 3)) + "}"
    path = tmp_path / "x.zip"
    path.write_bytes(make_export_zip({"x.json": body}))
    with pytest.raises(ExportIntegrityError, match="results"):
        read_export_stats(path)


def test_read_export_stats_reads_meta_results_placed_after_the_records(tmp_path):
    # Sample: "results" first, then meta with {skip 0, limit 3, total 3}. Key order is not
    # guaranteed by JSON, so meta.results must be found wherever it sits.
    body = '{"results": ' + json.dumps(records(1, 3)) + ', "meta": {"results": {"skip": 0, "limit": 3, "total": 3}}}'
    path = tmp_path / "x.zip"
    path.write_bytes(make_export_zip({"x.json": body}))
    assert read_export_stats(path).meta_results == {"skip": 0, "limit": 3, "total": 3}
