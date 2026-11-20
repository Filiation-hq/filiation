"""Unit tests for the record identity and field type checks run on every openFDA export (#169).

Task map:
    Input: every record of one source's export, streamed partition by partition with its
        partition file name and row index, before anything is stored.
    Output: identity findings. Fail: a record with no usable key, a key repeated by identical
        records, a key shared by records with different content, a declared identity, date
        or linkage field with the wrong type. Report: a record keyed by its fallback field,
        a top-level field not seen in the measured 2026-11 exports.
    Edge cases: a clean export; an empty export; a missing, blank, whitespace, null or
        non-string key; a non-object record; a blank key with and without a usable fallback;
        duplicates within one partition and across partitions; three copies of one key
        where only one differs; a list field turned into a string; a list holding a
        non-string; a declared field absent or null; an unknown source; more offenders than
        the example cap; records in a different order; the swap a count check can't see
        (one key gone, another doubled); a source's first snapshot, which the weekly diff
        never sees.
    Invariants: each finding locates its records by partition and row; examples are the
        smallest keys or locations, so the same export always gives the same findings; a
        failing identity check stops ingestion before any object is stored.

Real data behind the declared types (2026-11-18 510(k), 2026-11-20 recall, 2026-11-19
enforcement exports): every product-level key is unique, enforcement has one blank
recall_number (event 94520), no field is ever null, k_numbers and pma_numbers are always lists
of strings, and every other field is a string.
"""

import json
from datetime import datetime, timezone

import pytest
from filiation.core.storage.raw_store import LocalRawStore
from filiation.pipeline.crawler.identity import (
    FIELD_TYPES,
    KNOWN_FIELDS,
    RECORD_KEYS,
    IdentityProfile,
    record_key,
)
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError, ingest_export
from openfda_fakes import (
    PART_URL,
    export_body,
    load_index,
    make_export_zip,
    opener_for,
    opener_serving,
)

RECALL_PART = "device-recall-0001-of-0001.json.zip"


def identity_of(records: list, source: str = "510k", partition: str = "p1.json.zip") -> IdentityProfile:
    """Runs a fresh IdentityProfile over records from one partition.

    Args:
        records: Records to observe, in row order.
        source: openFDA source name.
        partition: Partition file name the records come from.

    Returns:
        IdentityProfile: The profile after observing every record.
    """
    profile = IdentityProfile(source)
    for row, record in enumerate(records):
        profile.observe(record, partition, row)
    return profile


def by_check(profile: IdentityProfile) -> dict:
    """Indexes a profile's findings by check id.

    Args:
        profile: A profile that has observed its records.

    Returns:
        dict: Finding per check id.
    """
    return {f.check.check_id: f for f in profile.findings()}


def recall(number: str, **extra) -> dict:
    """Builds a device/recall record with its key, event and k_numbers.

    Args:
        number: product_res_number.
        **extra: Other fields, overriding the defaults.

    Returns:
        dict: The record.
    """
    return {"product_res_number": number, "res_event_number": "90001", "k_numbers": ["K000001"], **extra}


def test_identity_check_finds_nothing_in_a_clean_export():
    # Sample: three distinct 510(k) records with string dates and product codes, the shape of
    # every one of the 176,683 records in the 2026-11-18 export.
    records = [{"k_number": f"K00000{i}", "decision_date": "2020-03-04", "product_code": "DQA"} for i in range(3)]
    assert identity_of(records).findings() == []


def test_identity_check_finds_nothing_in_an_empty_export():
    # Sample: no records at all. Nothing can be duplicated or mistyped; the count checks own
    # whether an empty export is acceptable.
    assert identity_of([]).findings() == []


def test_identity_check_fails_a_key_shared_by_records_with_different_content():
    # Sample: K000002 at rows 1 and 2 with different applicants. A keyed join would keep
    # one and silently drop the other, so the run must stop.
    records = [{"k_number": "K000001"}, {"k_number": "K000002", "applicant": "A"}, {"k_number": "K000002", "applicant": "B"}]
    finding = by_check(identity_of(records))["identity.duplicate_conflicting"]
    assert finding.check.severity == "fail"
    assert "K000002 (p1.json.zip rows 1, 2)" in finding.message


def test_identity_check_fails_an_exact_duplicate_row_under_its_own_check():
    # Sample: K000002 twice with identical content. No content conflicts, but one row is
    # extra, and an exact copy can hide a record lost from the same export.
    records = [{"k_number": "K000001"}, {"k_number": "K000002"}, {"k_number": "K000002"}]
    found = by_check(identity_of(records))
    assert set(found) == {"identity.duplicate_exact"}
    assert found["identity.duplicate_exact"].check.severity == "fail"
    assert "3 rows, 2 distinct keys" in found["identity.duplicate_exact"].message


def test_identity_check_calls_three_copies_conflicting_when_only_one_differs():
    # Sample: K000001 at rows 0, 1, 2; rows 0 and 1 identical, row 2 has a new applicant.
    records = [{"k_number": "K000001"}, {"k_number": "K000001"}, {"k_number": "K000001", "applicant": "B"}]
    found = by_check(identity_of(records))
    assert set(found) == {"identity.duplicate_conflicting"}
    assert "K000001 (p1.json.zip rows 0, 1, 2)" in found["identity.duplicate_conflicting"].message


def test_identity_check_locates_a_key_duplicated_across_partitions_by_file_and_row():
    # Sample: K000001 is row 0 of part-1 and row 1 of part-2. Each partition alone is clean.
    profile = IdentityProfile("510k")
    profile.observe({"k_number": "K000001"}, "part-1.json.zip", 0)
    profile.observe({"k_number": "K000002"}, "part-2.json.zip", 0)
    profile.observe({"k_number": "K000001"}, "part-2.json.zip", 1)
    message = by_check(profile)["identity.duplicate_exact"].message
    assert "K000001 (part-1.json.zip row 0, part-2.json.zip row 1)" in message


@pytest.mark.parametrize(
    "bad",
    [{"applicant": "No key"}, {"k_number": ""}, {"k_number": "   "}, {"k_number": None}, {"k_number": 30123}, ["not", "a", "record"]],
    ids=["missing", "empty", "whitespace", "null", "non-string", "non-object"],
)
def test_identity_check_fails_a_record_without_a_usable_key(bad):
    # Sample: one good 510(k) record, then one record whose k_number can't key it (510(k)
    # has no fallback field). Its location is reported because it has no key to name.
    finding = by_check(identity_of([{"k_number": "K000001"}, bad]))["identity.key_missing"]
    assert finding.check.severity == "fail"
    assert "p1.json.zip row 1" in finding.message


def test_identity_check_reports_a_blank_enforcement_key_keyed_by_its_event_id():
    # Sample: the real 2026-11-16 enforcement record with recall_number "" in event 94520.
    # The weekly diff keys it as "event_id=94520"; it's reported, never fatal.
    records = [{"recall_number": "Z-0001-2026", "event_id": "90001"}, {"recall_number": "", "event_id": "94520"}]
    found = by_check(identity_of(records, "enforcement"))
    assert set(found) == {"identity.fallback_key"}
    assert found["identity.fallback_key"].check.severity == "report"
    assert "event_id=94520 (p1.json.zip row 1)" in found["identity.fallback_key"].message


def test_identity_check_fails_a_blank_enforcement_key_whose_event_id_is_blank_too():
    # Sample: recall_number "" and event_id "": neither field can key the record.
    found = by_check(identity_of([{"recall_number": "", "event_id": ""}], "enforcement"))
    assert set(found) == {"identity.key_missing"}


def test_identity_check_fails_two_keyless_records_that_fall_back_to_the_same_event():
    # Sample: two blank-recall_number records in event 94520 with different products; their
    # fallback keys collide, so they can't be told apart.
    records = [
        {"recall_number": "", "event_id": "94520", "product_description": "SYR 10ML"},
        {"recall_number": "", "event_id": "94520", "product_description": "SYR 60ML"},
    ]
    assert "identity.duplicate_conflicting" in by_check(identity_of(records, "enforcement"))


def test_record_key_falls_back_only_when_the_key_is_blank():
    # Sample: enforcement keying. A real key wins over event_id; a blank one falls back; a
    # missing fallback leaves the record unkeyable.
    assert record_key({"recall_number": "Z-1", "event_id": "9"}, "recall_number", "event_id") == ("Z-1", False)
    assert record_key({"recall_number": "", "event_id": "9"}, "recall_number", "event_id") == ("event_id=9", True)
    assert record_key({"recall_number": ""}, "recall_number", "event_id") == (None, False)
    assert record_key({"recall_number": ""}, "recall_number", None) == (None, False)


def test_identity_check_fails_k_numbers_changed_from_an_array_to_a_string():
    # Sample: recall Z-0002 with k_numbers "K000001" instead of ["K000001"]. RecallRecord
    # would read the string as seven one-character K-numbers.
    records = [recall("Z-0001"), recall("Z-0002", k_numbers="K000001")]
    finding = by_check(identity_of(records, "recall"))["identity.field_type"]
    assert finding.check.severity == "fail"
    assert "k_numbers 1" in finding.message
    assert "Z-0002 k_numbers is str, expected list[str] (p1.json.zip row 1)" in finding.message


def test_identity_check_fails_a_list_field_holding_a_non_string_entry():
    # Sample: pma_numbers [850020] (an int) instead of ["P850020"].
    finding = by_check(identity_of([recall("Z-0001", pma_numbers=[850020])], "recall"))["identity.field_type"]
    assert "pma_numbers is list[int], expected list[str]" in finding.message


def test_identity_check_fails_a_date_field_that_is_no_longer_a_string():
    # Sample: a 510(k) decision_date sent as the number 20200304. Format errors in a
    # string date are the date rules' job; a type change is this check's.
    finding = by_check(identity_of([{"k_number": "K000001", "decision_date": 20200304}]))["identity.field_type"]
    assert "K000001 decision_date is int, expected str" in finding.message


def test_identity_check_accepts_a_declared_field_that_is_absent_or_null():
    # Sample: a recall with no k_numbers (20,792 real recalls lack it) and one with
    # pma_numbers null. Both read as "none" downstream; emptiness is completeness's job.
    records = [{"product_res_number": "Z-0001", "res_event_number": "1"}, recall("Z-0002", pma_numbers=None)]
    assert identity_of(records, "recall").findings() == []


def test_identity_check_accepts_an_empty_list_in_a_list_field():
    # Sample: k_numbers [] on a recall; an empty list is still a list of strings.
    assert identity_of([recall("Z-0001", k_numbers=[])], "recall").findings() == []


def test_identity_check_reports_an_unexpected_top_level_field_without_failing():
    # Sample: a 510(k) record carrying "submission_type", a field no 2026-11 export had.
    found = by_check(identity_of([{"k_number": "K000001", "submission_type": "Traditional"}]))
    assert set(found) == {"identity.unexpected_field"}
    assert found["identity.unexpected_field"].check.severity == "report"
    assert "submission_type (1 record)" in found["identity.unexpected_field"].message


def test_identity_check_caps_examples_at_five_and_counts_the_rest():
    # Sample: seven keys each duplicated once. The message counts all seven and lists the
    # five smallest keys, then an ellipsis.
    records = [{"k_number": f"K00000{i}"} for i in range(7)] * 2
    message = by_check(identity_of(records))["identity.duplicate_exact"].message
    assert message.startswith("7 keys")
    assert "K000004" in message and "K000005" not in message and message.endswith(", …")


def test_identity_check_gives_the_same_findings_whatever_the_record_order():
    # Sample: the same duplicated and mistyped records, forwards and reversed. Examples are
    # the smallest keys, so reruns and reordered exports report identically (rows differ).
    records = [recall(f"Z-000{i}") for i in range(4)] + [recall("Z-0001"), recall("Z-0003", k_numbers="K1")]
    forward = identity_of(records, "recall").findings()
    backward = identity_of(records[::-1], "recall").findings()
    assert [f.check.check_id for f in forward] == [f.check.check_id for f in backward]
    assert all("Z-0001" in f.message or "Z-0003" in f.message for f in forward + backward)


def test_identity_check_rejects_an_unknown_source():
    # Sample: "maude", which has no declared key field.
    with pytest.raises(ValueError, match="maude"):
        IdentityProfile("maude")


def test_every_source_declares_its_key_and_types_only_fields_it_knows():
    # Sample: the declared tables. A type for a field outside KNOWN_FIELDS, or a source with
    # no key, would be a check that can never run on real data.
    assert set(FIELD_TYPES) == set(KNOWN_FIELDS) == set(RECORD_KEYS)
    for source, types in FIELD_TYPES.items():
        assert RECORD_KEYS[source] in KNOWN_FIELDS[source]
        assert set(types) <= KNOWN_FIELDS[source]
        assert set(types.values()) <= {"str", "list[str]"}


def test_identity_check_lists_its_check_ids():
    # Sample: a fresh profile; every identity check runs on every source.
    assert IdentityProfile("recall").checks == [
        "identity.duplicate_conflicting",
        "identity.duplicate_exact",
        "identity.fallback_key",
        "identity.field_type",
        "identity.key_missing",
        "identity.unexpected_field",
    ]


def test_ingest_export_fails_a_swapped_key_that_the_count_checks_pass(tmp_path):
    # Sample: the index and meta.results promise 3 records and the zip holds 3, but K000003
    # is gone and K000002 appears twice. Every count reconciles; identity must not.
    records = [{"k_number": "K000001"}, {"k_number": "K000002"}, {"k_number": "K000002"}]
    zip_bytes = make_export_zip({"device-510k-0001-of-0001.json": export_body(records)})
    store = LocalRawStore(tmp_path / "raw")
    with pytest.raises(ExportIntegrityError) as raised:
        ingest_export("510k", store, opener=opener_for(zip_bytes))
    assert [f.check.check_id for f in raised.value.findings] == ["identity.duplicate_exact"]
    assert list(store.list("openfda/")) == []


def test_ingest_export_fails_a_recall_whose_k_numbers_became_a_string_before_storing(tmp_path):
    # Sample: the recall partition of the index fixture (2 records), with Z-0002's k_numbers
    # sent as a string. Nothing may be stored for a snapshot downstream code would misread.
    body = export_body([recall("Z-0001-2026"), recall("Z-0002-2026", k_numbers="K000001")])
    opener = opener_for(make_export_zip({"device-510k-0001-of-0001.json": export_body([])}))
    opener.responses[f"https://download.open.fda.gov/device/recall/{RECALL_PART}"] = make_export_zip(
        {RECALL_PART.removesuffix(".zip"): body}
    )
    store = LocalRawStore(tmp_path / "raw")
    with pytest.raises(ExportIntegrityError, match="k_numbers"):
        ingest_export("recall", store, opener=opener)
    assert list(store.list("openfda/")) == []


def test_ingest_export_locates_a_duplicate_split_across_two_partitions(tmp_path):
    # Sample: two 510(k) partitions of 2 records; K000001 is in both. Each partition's
    # counts are right, so only the identity check sees it.
    first = export_body([{"k_number": "K000001"}, {"k_number": "K000002"}], skip=0, total=4)
    second = export_body([{"k_number": "K000003"}, {"k_number": "K000001"}], skip=2, total=4)
    opener = opener_serving([(PART_URL.format(n=1), first, 2), (PART_URL.format(n=2), second, 2)])
    with pytest.raises(ExportIntegrityError, match=r"device-510k-0002-of-0002\.json\.zip row 1"):
        ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener)


def test_ingest_export_records_identity_checks_and_report_findings(tmp_path):
    # Sample: a clean 510(k) export plus one record with an unexpected field. The run goes
    # on; the report finding and every identity check id are on the result.
    records = [{"k_number": "K000001"}, {"k_number": "K000002"}, {"k_number": "K000003", "submission_type": "Special"}]
    zip_bytes = make_export_zip({"device-510k-0001-of-0001.json": export_body(records)})
    result = ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_for(zip_bytes))
    assert {"identity.duplicate_exact", "identity.field_type", "export_counts.total"} <= set(result.checks)
    assert [f.check.check_id for f in result.findings] == ["identity.unexpected_field"]


def test_first_snapshot_of_a_source_is_identity_checked_although_there_is_nothing_to_diff(tmp_path):
    # Sample: an empty raw store (no latest.json), so the weekly diff has no previous
    # snapshot and never runs. A duplicated key must still stop the run, and no manifest or
    # latest.json may be written.
    from filiation.pipeline.crawler.ingestion_run import run_ingestion

    records = [{"k_number": "K000001"}, {"k_number": "K000001", "applicant": "B"}, {"k_number": "K000003"}]
    zip_bytes = make_export_zip({"device-510k-0001-of-0001.json": export_body(records)})
    store = LocalRawStore(tmp_path / "raw")
    with pytest.raises(ExportIntegrityError, match="K000001"):
        run_ingestion(
            ["510k"],
            store,
            run_suffix="1",
            commit_sha="f7d257a" + "0" * 33,
            writer="local",
            opener=opener_for(zip_bytes, index=load_index()),
            clock=lambda: datetime(2026, 11, 25, 7, tzinfo=timezone.utc),
            work_dir=tmp_path,
        )
    assert not store.exists("latest.json") and list(store.list("runs/")) == []


def test_identity_findings_serialize_into_a_run_manifest_finding():
    # Sample: the report finding for event 94520, as the manifest stores it.
    finding = identity_of([{"recall_number": "", "event_id": "94520"}], "enforcement").findings()[0]
    assert json.loads(json.dumps(finding.to_dict()))["register_ids"] == ["R14"]
