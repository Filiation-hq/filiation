"""Unit tests for per-run field completeness and 510(k) date rules (#92, ADR-016, R14).

Task map:
    Input: the records of one source's export, streamed once (the same pass that counts
        records), plus the source name and its export_date.
    Output: fields_present (non-empty count per JSON-pointer path, 0 for paths seen but always
        empty), the check ids applied, and report-severity findings tied to R14. Nothing here
        stops a run: completeness and date anomalies are reported loudly, never dropped.
    Empty means None, "", a whitespace-only string, [], or {}. 0 and False are values.
    Date rules (510(k) only; recall dates are validated in linkage under ADR-013): an
        unparseable date; a decision before the Medical Device Amendments (1976-05-28); any
        date after the export_date; a decision before receipt. A receipt before the MDA is
        allowed: K760001-K760003 were received 1976-05-26 and are real (measured 2026-11-19).
    Edge cases: nested objects (openfda/*) and keys containing "/" or "~" (JSON-pointer
        escaping); a key present in some records and absent in others; a field seen but
        always empty; a record that is not an object; more distinct paths than the cap
        (a source keyed by ids would bloat the manifest); an impossible calendar date
        (2026-02-30); a missing date (completeness, not a date finding); a date exactly on
        the MDA or the export_date (allowed); thousands of anomalies (one finding with a
        count and at most 5 examples, chosen independent of record order); a record with no
        k_number in an example; a source with no date rules.
    Invariants: the same records in any order give the same fields_present and findings;
        memory stays bounded by the number of paths, not records.

The module under test is imported inside each test so this file imports on the base branch (#137).
"""

import importlib
import json

import pytest
from openfda_fakes import export_body, make_export_zip, opener_for
from recension.core.storage.raw_store import LocalRawStore
from recension.pipeline.crawler.openfda_bulk import ingest_export


def profile_of(records: list, source: str = "510k", export_date: str = "2026-11-09"):
    """Profiles records with a fresh ExportProfile.

    Args:
        records: Records to observe, in order.
        source: Source name.
        export_date: The export's date.

    Returns:
        ExportProfile: The profile after observing every record.
    """
    module = importlib.import_module("recension.pipeline.crawler.completeness")
    profile = module.ExportProfile(source, export_date)
    for record in records:
        profile.observe(record)
    return profile


def by_check(profile) -> dict:
    """Indexes a profile's findings by check id.

    Args:
        profile: An ExportProfile.

    Returns:
        dict: check id -> Finding.
    """
    return {f.check.check_id: f for f in profile.findings()}


def k(number: str, received: str = "2020-01-02", decided: str = "2020-03-04", **extra) -> dict:
    """Builds a 510(k) record with dates.

    Args:
        number: k_number.
        received: date_received.
        decided: decision_date.
        **extra: Other fields.

    Returns:
        dict: The record.
    """
    return {"k_number": number, "date_received": received, "decision_date": decided, **extra}


def test_fields_present_counts_non_empty_values_per_json_pointer_path():
    # Sample: 3 records; contact filled in 2; openfda/device_class filled in 1 (nested).
    records = [
        k("K000001", contact="A", openfda={"device_class": "2"}),
        k("K000002", contact="B", openfda={}),
        k("K000003"),
    ]
    present = profile_of(records).fields_present
    assert present["/k_number"] == 3
    assert present["/contact"] == 2
    assert present["/openfda"] == 1
    assert present["/openfda/device_class"] == 1


@pytest.mark.parametrize("empty", [None, "", "   ", [], {}], ids=["null", "empty-string", "whitespace", "empty-list", "empty-object"])
def test_fields_present_counts_null_blank_and_empty_containers_as_empty(empty):
    # Sample: one record whose contact is null, "", "   ", [] or {}. openFDA uses "" for
    # absent text (address_2 is "" in 82% of 510(k)s), so "" must not count as filled.
    assert profile_of([k("K000001", contact=empty)]).fields_present["/contact"] == 0


@pytest.mark.parametrize("value", [0, False], ids=["zero", "false"])
def test_fields_present_counts_zero_and_false_as_values(value):
    # Sample: a count of 0 or a false flag. They are data, not missing data.
    assert profile_of([k("K000001", flag=value)]).fields_present["/flag"] == 1


def test_fields_present_escapes_slash_and_tilde_in_keys():
    # Sample: keys "a/b" and "c~d" at top level, and a nested a -> b. Unescaped, "a/b" and
    # the nested path would collide on "/a/b".
    present = profile_of([{"a/b": "x", "c~d": "y", "a": {"b": "z"}}]).fields_present
    assert present["/a~1b"] == 1 and present["/c~0d"] == 1 and present["/a/b"] == 1


def test_field_seen_but_always_empty_is_counted_as_zero_and_reported():
    # Sample: expedited_review_flag is "" in all 3 records. A field that exists but never
    # holds data is exactly R14's "silently empty field".
    profile = profile_of([k(f"K00000{i}", expedited_review_flag="") for i in range(3)])
    assert profile.fields_present["/expedited_review_flag"] == 0
    finding = by_check(profile)["completeness.field_always_empty"]
    assert "/expedited_review_flag" in finding.message
    assert finding.check.severity == "report" and finding.check.register_ids == ("R14",)


def test_record_that_is_not_an_object_is_reported_and_not_profiled():
    # Sample: two good records and the string "oops" in the results array.
    profile = profile_of([k("K000001"), "oops", k("K000002")])
    assert "1 record" in by_check(profile)["completeness.non_object_record"].message
    assert profile.fields_present["/k_number"] == 2


def test_field_paths_beyond_the_cap_are_dropped_and_reported():
    # Sample: one record with MAX_FIELDS + 3 distinct keys, as an id-keyed object would have.
    module = importlib.import_module("recension.pipeline.crawler.completeness")
    record = {f"f{i}": "x" for i in range(module.MAX_FIELDS + 3)}
    profile = profile_of([record])
    assert len(profile.fields_present) == module.MAX_FIELDS
    assert "3" in by_check(profile)["completeness.field_cap"].message


def test_decision_before_the_medical_device_amendments_is_reported():
    # Sample: K750001 decided 1976-05-27, one day before the MDA. No 510(k) clearance can
    # predate the law that created it.
    finding = by_check(profile_of([k("K750001", "1976-01-02", "1976-05-27")]))["dates.decision_before_mda"]
    assert "K750001" in finding.message


def test_receipt_before_the_medical_device_amendments_is_not_reported():
    # Sample: K760001 as in the real export: received 1976-05-26, decided 1976-07-26. The
    # first 510(k)s were filed two days before the MDA took effect; flagging them is wrong.
    assert by_check(profile_of([k("K760001", "1976-05-26", "1976-07-26")])) == {}


def test_decision_exactly_on_the_medical_device_amendments_date_is_allowed():
    # Sample: decision_date 1976-05-28, the boundary itself.
    assert "dates.decision_before_mda" not in by_check(profile_of([k("K760009", "1976-05-28", "1976-05-28")]))


def test_date_after_the_export_date_is_reported():
    # Sample: export 2026-11-09; one record decided 2026-11-10. A record can't describe a
    # day after the export that contains it.
    finding = by_check(profile_of([k("K261234", "2026-11-01", "2026-11-10")]))["dates.after_export"]
    assert "K261234" in finding.message


def test_date_exactly_on_the_export_date_is_allowed():
    # Sample: decided on the export date itself, 2026-11-09.
    assert by_check(profile_of([k("K261235", "2026-11-01", "2026-11-09")])) == {}


def test_decision_before_receipt_is_reported():
    # Sample: received 2020-03-04, decided 2020-01-02.
    finding = by_check(profile_of([k("K200001", "2020-03-04", "2020-01-02")]))["dates.decision_before_received"]
    assert "K200001" in finding.message


@pytest.mark.parametrize("bad", ["2026-02-30", "20200102", "unknown"], ids=["impossible-day", "compact", "text"])
def test_unparseable_date_is_reported_and_skips_the_other_date_rules(bad):
    # Sample: decision_date "2026-02-30", "20200102" or "unknown". It is reported once as
    # unparseable; comparing it with other dates would be meaningless.
    found = by_check(profile_of([k("K200002", decided=bad)]))
    assert set(found) == {"dates.unparseable"}
    assert "decision_date" in found["dates.unparseable"].message


def test_missing_date_is_completeness_not_a_date_finding():
    # Sample: decision_date "" in one record. Missing data shows in fields_present only.
    profile = profile_of([k("K200003", decided="")])
    assert profile.fields_present["/decision_date"] == 0
    assert not any(cid.startswith("dates.") for cid in by_check(profile))


def test_many_anomalies_give_one_finding_with_a_count_and_five_examples():
    # Sample: 1,000 records decided before receipt. One finding per check keeps the manifest
    # small; its examples are the 5 smallest K-numbers.
    records = [k(f"K{i:06d}", "2020-03-04", "2020-01-02") for i in range(1000, 0, -1)]
    finding = by_check(profile_of(records))["dates.decision_before_received"]
    assert "1,000 records" in finding.message
    assert "K000001, K000002, K000003, K000004, K000005" in finding.message
    assert "K000006" not in finding.message


def test_findings_and_fields_do_not_depend_on_record_order():
    # Sample: the same 4 records (two anomalies) forwards and backwards.
    records = [k("K000004", decided="2026-12-01"), k("K000001"), k("K000003", decided="2026-12-02"), k("K000002", contact="")]
    forward, backward = profile_of(records), profile_of(records[::-1])
    assert forward.fields_present == backward.fields_present
    assert [f.to_dict() for f in forward.findings()] == [f.to_dict() for f in backward.findings()]


def test_anomaly_on_a_record_without_a_k_number_names_its_position():
    # Sample: a record with no k_number decided after the export, at index 0.
    finding = by_check(profile_of([{"date_received": "2026-11-01", "decision_date": "2026-12-01"}]))["dates.after_export"]
    assert "#0" in finding.message


def test_recall_source_gets_completeness_checks_but_no_510k_date_rules():
    # Sample: a recall record. Recall dates are validated in linkage (ADR-013), so only the
    # completeness checks apply here.
    profile = profile_of([{"product_res_number": "Z-0001-2026", "decision_date": "1900-01-01"}], source="recall")
    assert by_check(profile) == {}
    assert profile.checks and not any(cid.startswith("dates.") for cid in profile.checks)


def test_510k_profile_lists_the_date_rules_among_its_checks():
    # Sample: an empty 510(k) profile. checks names every rule applied, even with no findings.
    checks = profile_of([]).checks
    assert {"dates.unparseable", "dates.decision_before_mda", "dates.after_export", "dates.decision_before_received"} <= set(checks)


def test_ingest_export_returns_completeness_checks_and_findings_for_the_source(tmp_path):
    # Sample: a 3-record 510(k) export where one record is decided after the 2026-11-09 export.
    records = [k("K000001"), k("K000002"), k("K000003", decided="2026-11-30")]
    zip_bytes = make_export_zip({"device-510k-0001-of-0001.json": export_body(records)})
    result = ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_for(zip_bytes))
    assert result.fields_present["/k_number"] == 3
    assert "export_counts.total" in result.checks and "dates.after_export" in result.checks
    assert [f.check.check_id for f in result.findings] == ["dates.after_export"]


def test_ingest_export_profiles_every_partition_into_one_source_profile(tmp_path):
    # Sample: two partitions of 2 records; contact filled once in each. Completeness is per
    # source, not per partition.
    from openfda_fakes import PART_URL, opener_serving, records

    first = [{**r, "contact": "A"} for r in records(1, 1)] + records(2, 1)
    second = [{**r, "contact": "B"} for r in records(3, 1)] + records(4, 1)
    parts = [
        (PART_URL.format(n=1), export_body(first, skip=0, total=4), 2),
        (PART_URL.format(n=2), export_body(second, skip=2, total=4), 2),
    ]
    result = ingest_export("510k", LocalRawStore(tmp_path / "raw"), opener=opener_serving(parts))
    assert result.fields_present["/contact"] == 2 and result.fields_present["/k_number"] == 4


def test_run_ingestion_writes_completeness_into_the_manifest(tmp_path):
    # Sample: the fixture run (510(k), recall, enforcement). Each source in the manifest
    # carries fields_present, the checks that ran, and its findings list.
    from datetime import datetime, timezone

    from recension.core.schemas.run_manifest import RunManifest
    from recension.pipeline.crawler.ingestion_run import run_ingestion

    zip_bytes = make_export_zip({"device-510k-0001-of-0001.json": export_body([k("K000001"), k("K000002"), k("K000003")])})
    manifest = run_ingestion(
        ["510k", "recall", "enforcement"],
        LocalRawStore(tmp_path / "raw"),
        run_suffix="1",
        commit_sha="f7d257a" + "0" * 33,
        writer="local",
        opener=opener_for(zip_bytes),
        clock=lambda: datetime(2026, 11, 16, 6, tzinfo=timezone.utc),
        work_dir=tmp_path,
    )
    stored = RunManifest.from_dict(json.loads((tmp_path / "raw" / manifest.manifest_key).read_text()))
    assert [s.source for s in stored.sources] == ["510k", "recall", "enforcement"]
    for source in stored.sources:
        assert source.fields_present and source.checks and source.findings == []
