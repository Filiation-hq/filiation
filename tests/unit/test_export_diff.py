"""Unit tests for the weekly export diff in recension.pipeline.crawler.export_diff (#40).

Task map:
    Input: one source's previous and current export zips (one or more partitions each) and
        the field that keys its records: k_number for 510(k), product_res_number for recall.
    Output: counts of records added, removed, and amended, plus how many amended records
        changed each top-level field.
    Edge cases: a key-order-only change (not an amendment); a field added to or dropped
        from a record (counts as changed); records spread across several partitions; a
        duplicate key, in one partition or across two; a record with a missing, empty, or
        non-string key; a record that is not a JSON object; two empty exports.
    Invariants: the same pair of exports always gives the same diff; memory holds one
        digest per key plus field digests for amended records only, never whole exports
        (the real 510(k) export inflates to ~1.6 GB).
"""

import json

import pytest
from openfda_fakes import make_export_zip
from recension.pipeline.crawler.export_diff import RECORD_KEYS, RecordDiff, diff_exports
from recension.pipeline.crawler.openfda_bulk import ExportIntegrityError

STRYKER = {"k_number": "K210758", "applicant": "Stryker", "decision_date": "2021-04-15", "openfda": {}}
CATHETER = {"k_number": "K030123", "applicant": "Example Medical", "decision_date": "2003-05-02"}
STENT = {"k_number": "K882211", "applicant": "Legacy Devices Co", "decision_date": "1988-09-30"}


def write_export(path, records):
    """Writes records as a one-file openFDA export zip.

    Args:
        path: Zip file to write.
        records: Records for the "results" array.

    Returns:
        Path: path, now holding the zip.
    """
    body = json.dumps({"meta": {"last_updated": "2026-11-09"}, "results": records})
    path.write_bytes(make_export_zip({"export.json": body}))
    return path


def test_diff_exports_counts_one_add_one_removal_and_one_amendment(tmp_path):
    # Sample: last week holds STRYKER, CATHETER, STENT; this week drops STENT, adds
    # K251234, and STRYKER's decision_date was corrected. Expect 1/1/1 and only
    # decision_date listed as changed.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER, STENT])
    amended = {**STRYKER, "decision_date": "2021-04-16"}
    new = {"k_number": "K251234", "applicant": "New Co", "decision_date": "2025-10-01"}
    current = write_export(tmp_path / "cur.zip", [amended, CATHETER, new])

    diff = diff_exports("k_number", [previous], [current])

    assert diff == RecordDiff(added=1, removed=1, amended=1, changed_fields={"decision_date": 1})


def test_diff_exports_ignores_a_change_in_key_order_within_a_record(tmp_path):
    # Sample: CATHETER serialized with its keys reversed this week. openFDA does not
    # promise key order, so this is the same record, not an amendment.
    previous = write_export(tmp_path / "prev.zip", [CATHETER])
    reordered = dict(reversed(list(CATHETER.items())))
    current = write_export(tmp_path / "cur.zip", [reordered])

    assert diff_exports("k_number", [previous], [current]) == RecordDiff(0, 0, 0, {})


def test_diff_exports_lists_a_field_added_or_dropped_as_changed(tmp_path):
    # Sample: STRYKER gains a "statement_or_summary" field; CATHETER loses "applicant".
    # Both are amendments, and each record's changed field is named.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER])
    catheter = {k: v for k, v in CATHETER.items() if k != "applicant"}
    current = write_export(tmp_path / "cur.zip", [{**STRYKER, "statement_or_summary": "Summary"}, catheter])

    diff = diff_exports("k_number", [previous], [current])

    assert diff == RecordDiff(0, 0, 2, {"applicant": 1, "statement_or_summary": 1})


def test_diff_exports_counts_how_many_amended_records_changed_each_field(tmp_path):
    # Sample: openFDA re-enriches both STRYKER and CATHETER (new "openfda" block) and also
    # corrects CATHETER's decision_date. Expect openfda: 2, decision_date: 1.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER, STENT])
    enriched = {"device_class": "2"}
    current = write_export(
        tmp_path / "cur.zip",
        [{**STRYKER, "openfda": enriched}, {**CATHETER, "openfda": enriched, "decision_date": "2003-05-03"}, STENT],
    )

    diff = diff_exports("k_number", [previous], [current])

    assert diff == RecordDiff(0, 0, 2, {"decision_date": 1, "openfda": 2})


def test_diff_exports_matches_records_across_partitions(tmp_path):
    # Sample: last week STRYKER and CATHETER are in one partition; this week openFDA splits
    # the export into two partitions holding one record each. Nothing changed.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER])
    part1 = write_export(tmp_path / "cur-1.zip", [CATHETER])
    part2 = write_export(tmp_path / "cur-2.zip", [STRYKER])

    assert diff_exports("k_number", [previous], [part1, part2]) == RecordDiff(0, 0, 0, {})


def test_diff_exports_gives_the_same_diff_when_rerun_on_the_same_exports(tmp_path):
    # Sample: the add/remove/amend pair from the first test, diffed twice. The weekly
    # manifest must not depend on dict or set iteration order.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER, STENT])
    current = write_export(tmp_path / "cur.zip", [{**STRYKER, "openfda": {"x": 1}, "applicant": "S"}, CATHETER])

    first = diff_exports("k_number", [previous], [current])
    second = diff_exports("k_number", [previous], [current])

    assert first == second
    assert list(first.changed_fields) == ["applicant", "openfda"]


def test_diff_exports_handles_two_empty_exports(tmp_path):
    # Sample: both weeks have an empty "results" array. Nothing to compare; all counts 0.
    previous = write_export(tmp_path / "prev.zip", [])
    current = write_export(tmp_path / "cur.zip", [])
    assert diff_exports("k_number", [previous], [current]) == RecordDiff(0, 0, 0, {})


@pytest.mark.parametrize(
    "records",
    [
        [STRYKER, {**STRYKER, "applicant": "Other"}],
        [STRYKER, {"applicant": "No key"}],
        [STRYKER, {**CATHETER, "k_number": ""}],
        [STRYKER, {**CATHETER, "k_number": None}],
        [STRYKER, {**CATHETER, "k_number": 30123}],
        [STRYKER, ["not", "an", "object"]],
    ],
    ids=["duplicate-key", "missing-key", "empty-key", "null-key", "non-string-key", "non-object-record"],
)
def test_diff_exports_rejects_records_it_cannot_key(tmp_path, records):
    # Sample: a current export with one bad record next to STRYKER. A duplicate makes
    # "amended" ambiguous, and an unkeyed record can't be matched, so the run must stop
    # rather than report a wrong diff.
    previous = write_export(tmp_path / "prev.zip", [STRYKER])
    current = write_export(tmp_path / "cur.zip", records)
    with pytest.raises(ExportIntegrityError):
        diff_exports("k_number", [previous], [current])


def test_diff_exports_rejects_a_key_duplicated_across_partitions(tmp_path):
    # Sample: STRYKER appears in both partitions of the previous export. Each partition is
    # valid alone; together they hold two records for one K-number.
    part1 = write_export(tmp_path / "prev-1.zip", [STRYKER])
    part2 = write_export(tmp_path / "prev-2.zip", [STRYKER, CATHETER])
    current = write_export(tmp_path / "cur.zip", [STRYKER])
    with pytest.raises(ExportIntegrityError):
        diff_exports("k_number", [part1, part2], [current])


def test_record_keys_name_the_unique_field_of_each_source():
    # Sample: the three sources ingested weekly. 510k/recall verified 2026-11-13 against the
    # openFDA API (count=<field>.exact tops at 1); enforcement's recall_number verified
    # 2026-11-17 on the full 40,535-record export: present on every record, never repeated.
    assert RECORD_KEYS == {"510k": "k_number", "recall": "product_res_number", "enforcement": "recall_number"}
