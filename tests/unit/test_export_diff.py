"""Unit tests for the weekly export diff in filiation.pipeline.crawler.export_diff (#40).

Task map:
    Input: one source's previous and current export zips (one or more partitions each) and
        the field that keys its records: k_number for 510(k), product_res_number for recall.
    Output: counts of records added, removed, and amended, plus how many amended records
        changed each top-level field.
    Edge cases: a key-order-only change (not an amendment); a field added to or dropped
        from a record (counts as changed); records spread across several partitions; a
        duplicate key, in one partition or across two; a record with a missing, empty, or
        non-string key; a record that is not a JSON object; two empty exports. For sources
        with a fallback field (enforcement: event_id), a blank key is keyed by the fallback;
        a blank fallback or two records sharing one still fail.
    Invariants: the same pair of exports always gives the same diff; memory holds one
        digest per key plus field digests for amended records only, never whole exports
        (the real 510(k) export inflates to ~1.6 GB).

Per-key changes (#76, ADR-018):
    Output: one KeyChange per added, removed, or amended key, with an amended record's changed
        top-level fields, in key order; the counts are derived from the same list.
    Edge cases: exports in different record orders; a fallback-keyed enforcement record;
        identical exports (empty list).

Cosmetic changes (#91, ADR-021):
    Output: each amended change also names the fields whose change is cosmetic only: text that
        differs only in whitespace, or lists whose entries are only reordered, at any depth.
    Edge cases: a whitespace run inside text, or leading/trailing whitespace (cosmetic); a
        reordered top-level list or one nested in openfda (cosmetic); a list that gained,
        lost or duplicated an entry, a case change, a value blanked to "n/a", a field that
        appears or disappears (all substantive); a record with both kinds of change.
"""

import json

import pytest
from filiation.core.schemas import run_manifest as rm
from filiation.pipeline.crawler import export_diff as ed
from filiation.pipeline.crawler.export_diff import (
    FALLBACK_KEYS,
    RECORD_KEYS,
    RecordDiff,
    diff_exports,
)
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError
from openfda_fakes import make_export_zip

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


# The real 2026-11-16 enforcement export has one record with recall_number "" (event 94520,
# "Not Yet Classified"); without a fallback key the second weekly run would fail its diff.
KEYLESS_ENFORCEMENT = {"recall_number": "", "event_id": "94520", "classification": "Not Yet Classified", "status": "Ongoing"}
CLASSIFIED_ENFORCEMENT = {"recall_number": "Z-2372-2023", "event_id": "92815", "classification": "Class II", "status": "Ongoing"}


def test_fallback_keys_cover_only_enforcement_by_event_id():
    # Sample: the per-source fallback map. 510k and recall have no blank keys, so they keep
    # rejecting unkeyable records outright.
    assert FALLBACK_KEYS == {"enforcement": "event_id"}


def test_diff_keys_a_record_with_a_blank_key_by_its_fallback_field(tmp_path):
    # Sample: the keyless event-94520 record goes from "Ongoing" to "Terminated" between weeks,
    # next to an unchanged classified record. Keyed as event_id=94520, it is 1 amended record.
    terminated = {**KEYLESS_ENFORCEMENT, "status": "Terminated"}
    previous = write_export(tmp_path / "prev.zip", [CLASSIFIED_ENFORCEMENT, KEYLESS_ENFORCEMENT])
    current = write_export(tmp_path / "cur.zip", [CLASSIFIED_ENFORCEMENT, terminated])
    diff = diff_exports("recall_number", [previous], [current], fallback_field="event_id")
    assert diff == RecordDiff(added=0, removed=0, amended=1, changed_fields={"status": 1})


def test_diff_counts_a_keyless_record_that_gains_its_key_as_one_removed_and_one_added(tmp_path):
    # Sample: FDA later assigns event 94520's record the number Z-2043-2024. The old
    # event_id=94520 key disappears and Z-2043-2024 appears.
    numbered = {**KEYLESS_ENFORCEMENT, "recall_number": "Z-2043-2024"}
    previous = write_export(tmp_path / "prev.zip", [KEYLESS_ENFORCEMENT])
    current = write_export(tmp_path / "cur.zip", [numbered])
    diff = diff_exports("recall_number", [previous], [current], fallback_field="event_id")
    assert (diff.added, diff.removed, diff.amended) == (1, 1, 0)


def test_diff_rejects_a_record_whose_key_and_fallback_are_both_blank(tmp_path):
    # Sample: an enforcement record with recall_number "" and no event_id at all.
    record = {"recall_number": "", "classification": "Class II"}
    export = write_export(tmp_path / "e.zip", [record])
    with pytest.raises(ExportIntegrityError, match="event_id"):
        diff_exports("recall_number", [export], [export], fallback_field="event_id")


def test_diff_rejects_two_keyless_records_with_the_same_fallback_value(tmp_path):
    # Sample: two keyless records both in event 94520; their fallback keys collide.
    other = {**KEYLESS_ENFORCEMENT, "product_description": "SYR 60ML"}
    export = write_export(tmp_path / "e.zip", [KEYLESS_ENFORCEMENT, other])
    with pytest.raises(ExportIntegrityError, match="94520"):
        diff_exports("recall_number", [export], [export], fallback_field="event_id")


def test_diff_export_changes_lists_each_added_removed_and_amended_key_with_its_changed_fields(tmp_path):
    # Sample: last week STRYKER, CATHETER, STENT; this week STENT is gone, K251234 is new, and
    # STRYKER's decision_date and openfda block changed (the real weekly openfda churn).
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER, STENT])
    amended = {**STRYKER, "decision_date": "2021-04-16", "openfda": {"fei_number": ["3005985723"]}}
    current = write_export(tmp_path / "cur.zip", [amended, CATHETER, {"k_number": "K251234"}])

    assert ed.diff_export_changes("k_number", [previous], [current]) == [
        rm.KeyChange("K210758", "amended", ("decision_date", "openfda")),
        rm.KeyChange("K251234", "added"),
        rm.KeyChange("K882211", "removed"),
    ]


def test_diff_export_changes_come_out_in_key_order_whatever_the_export_order(tmp_path):
    # Sample: three new records written in reverse key order against an empty previous export.
    previous = write_export(tmp_path / "prev.zip", [])
    current = write_export(tmp_path / "cur.zip", [{"k_number": k} for k in ("K3", "K2", "K1")])
    assert [c.key for c in ed.diff_export_changes("k_number", [previous], [current])] == ["K1", "K2", "K3"]


def test_diff_export_changes_key_a_blank_recall_number_by_its_fallback(tmp_path):
    # Sample: the real enforcement record with a blank recall_number (event 94520), reclassified.
    # Its change must be listed under the same fallback key the counts use.
    before = {"recall_number": "", "event_id": "94520", "classification": "Not Yet Classified"}
    previous = write_export(tmp_path / "prev.zip", [before])
    current = write_export(tmp_path / "cur.zip", [{**before, "classification": "Class II"}])
    changes = ed.diff_export_changes("recall_number", [previous], [current], FALLBACK_KEYS["enforcement"])
    assert changes == [rm.KeyChange("event_id=94520", "amended", ("classification",))]


def test_diff_export_changes_of_identical_exports_is_empty(tmp_path):
    # Sample: the same two records in both weeks; nothing to list, so nothing to store.
    previous = write_export(tmp_path / "prev.zip", [STRYKER, CATHETER])
    current = write_export(tmp_path / "cur.zip", [CATHETER, STRYKER])
    assert ed.diff_export_changes("k_number", [previous], [current]) == []


def test_record_diff_counts_come_from_the_change_list():
    # Sample: one added, two amended (openfda twice, decision_date once), no removals.
    changes = [
        rm.KeyChange("K1", "added"),
        rm.KeyChange("K2", "amended", ("openfda",)),
        rm.KeyChange("K3", "amended", ("decision_date", "openfda")),
    ]
    assert ed.RecordDiff.from_changes(changes) == RecordDiff(1, 0, 2, {"decision_date": 1, "openfda": 2})


RECALL = {
    "product_res_number": "Z-0636-2013",
    "product_description": "Siemens CLINITEK Atlas Positive and Negative Control Strips",
    "action": "Siemens sent an Urgent Field Safety Notice.",
    "k_numbers": ["K003154", "K962155"],
    "openfda": {"fei_number": ["3002806549", "1000119087"], "device_name": "Strips"},
}


def recall_change(tmp_path, **changes):
    """Diffs RECALL against a copy of itself with the given fields replaced.

    Args:
        tmp_path: Directory for the two export zips.
        **changes: Field values for the current export.

    Returns:
        KeyChange: The one change the diff lists.
    """
    previous = write_export(tmp_path / "prev.zip", [RECALL])
    current = write_export(tmp_path / "cur.zip", [{**RECALL, **changes}])
    (change,) = ed.diff_export_changes("product_res_number", [previous], [current])
    return change


def test_diff_marks_a_whitespace_run_inside_text_as_cosmetic(tmp_path):
    # Sample: the real Nov 13 -> 18 recall churn, where 3,170 text fields changed only in runs
    # of whitespace (here a double space and a non-breaking space).
    change = recall_change(tmp_path, product_description="Siemens CLINITEK  Atlas Positive and\u00a0Negative Control Strips")
    assert change == rm.KeyChange("Z-0636-2013", "amended", ("product_description",), cosmetic=("product_description",))


def test_diff_marks_leading_or_trailing_whitespace_as_cosmetic(tmp_path):
    # Sample: the action text with a trailing space and newline added.
    change = recall_change(tmp_path, action="Siemens sent an Urgent Field Safety Notice. \n")
    assert change.cosmetic == ("action",)


def test_diff_marks_a_reordered_list_as_cosmetic(tmp_path):
    # Sample: the same two K-numbers in the other order, as in all 54 real k_numbers changes.
    change = recall_change(tmp_path, k_numbers=["K962155", "K003154"])
    assert change.cosmetic == ("k_numbers",)


def test_diff_marks_a_list_reordered_inside_openfda_as_cosmetic(tmp_path):
    # Sample: openfda.fei_number reordered, about half of the real weekly 510(k) openfda churn.
    change = recall_change(tmp_path, openfda={"fei_number": ["1000119087", "3002806549"], "device_name": "Strips"})
    assert change.cosmetic == ("openfda",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("k_numbers", ["K003154", "K962155", "K991234"]),
        ("k_numbers", ["K003154"]),
        ("k_numbers", ["K003154", "K003154", "K962155"]),
        ("openfda", {"fei_number": ["3002806549"], "device_name": "Strips"}),
        ("product_description", "SIEMENS CLINITEK Atlas Positive and Negative Control Strips"),
        ("product_description", "n/a"),
    ],
    ids=["list gained an entry", "list lost an entry", "list duplicated an entry", "openfda list lost an entry", "case change", "blanked to n/a"],
)
def test_diff_treats_a_change_in_content_as_substantive(tmp_path, field, value):
    # Sample: one real change of content per case; the last is the real Z-0774-03 recall,
    # whose description openFDA replaced with "n/a". None of these is cosmetic.
    change = recall_change(tmp_path, **{field: value})
    assert change.fields == (field,)
    assert change.cosmetic == ()


def test_diff_treats_a_field_that_appears_or_disappears_as_substantive(tmp_path):
    # Sample: the recall gains pma_numbers and loses action in the same week.
    previous = write_export(tmp_path / "prev.zip", [RECALL])
    current = {k: v for k, v in RECALL.items() if k != "action"} | {"pma_numbers": ["P990025"]}
    (change,) = ed.diff_export_changes("product_res_number", [previous], [write_export(tmp_path / "cur.zip", [current])])
    assert change == rm.KeyChange("Z-0636-2013", "amended", ("action", "pma_numbers"))


def test_diff_separates_cosmetic_from_substantive_fields_in_one_record(tmp_path):
    # Sample: whitespace-only action change plus a real new K-number in the same week.
    change = recall_change(tmp_path, action="Siemens  sent an Urgent Field Safety Notice.", k_numbers=["K003154", "K962155", "K991234"])
    assert change == rm.KeyChange("Z-0636-2013", "amended", ("action", "k_numbers"), cosmetic=("action",))
