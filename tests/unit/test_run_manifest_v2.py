"""Unit tests for run manifest schema v2: per-source completeness, checks run, and findings (ADR-016, #92).

Task map:
    Input: per source, fields_present (non-empty record count per JSON-pointer field path,
        0 for fields seen but always empty), checks (every check id that ran), and findings
        (report-severity results of those checks); or v1 manifests already in the store.
    Output: SourceSnapshot fields that validate at construction and round-trip through JSON;
        v1 manifests that still load, with the new fields None ("not checked").
    Edge cases: a field count above the source's record total or below 0; a field path not
        starting with "/"; the same check id listed twice; findings without checks; a finding
        whose check did not run; a "fail" finding in a written manifest (a fail stops the run
        before any manifest exists, so one here means a gate was bypassed); a malformed
        finding (bad severity, register id, or check id); a v1 document that carries v2
        fields; schema_version 3 or missing; a v1 manifest re-serialized (must stay v1, not
        silently claim checks that never ran); a source with checks but no findings (checked,
        clean) versus none (never checked).
    Invariants: new manifests are v2; fields_present serializes in path order so the same
        run always writes the same JSON; None and empty mean different things.
ManifestFinding is reached through the module so this file imports on the base branch (#137).
"""

import json

import pytest
from recension.core.schemas import run_manifest as rm
from recension.core.schemas.run_manifest import ManifestObject, RunManifest, SourceSnapshot

SHA = "eb479de53bcb8b48c73c9a5ecaf6eea75eb84a00431ac0c04000090005c663c9"
COMMIT = "f7d257a" + "0" * 33
KEY = "openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip"
PRE_MDA = {
    "check": "dates.decision_before_mda",
    "severity": "report",
    "register_ids": ["R14"],
    "subject": "510k",
    "message": "1 record decided before 1976-05-28: K750001",
}


def snapshot(records: int = 3, **overrides) -> SourceSnapshot:
    """Builds a checked 510(k) source snapshot with one partition.

    Args:
        records: Record count of its one object.
        **overrides: SourceSnapshot fields to replace.

    Returns:
        SourceSnapshot: The snapshot.
    """
    fields = {
        "source": "510k",
        "status": "stored",
        "export_date": "2026-11-09",
        "objects": [ManifestObject(KEY, SHA, 1000, records)],
        "fields_present": {"/k_number": 3, "/decision_date": 3, "/expedited_review_flag": 0},
        "checks": ["export_counts.total", "dates.decision_before_mda"],
        "findings": [rm.ManifestFinding(**PRE_MDA)],
    }
    fields.update(overrides)
    return SourceSnapshot(**fields)


def manifest(*sources: SourceSnapshot) -> RunManifest:
    """Builds a valid run manifest around the given sources.

    Args:
        *sources: Source snapshots for the run.

    Returns:
        RunManifest: The manifest.
    """
    return RunManifest(
        run_id="2026-11-23_1",
        run_date="2026-11-23",
        started_at="2026-11-23T06:00:00Z",
        finished_at="2026-11-23T06:01:00Z",
        commit_sha=COMMIT,
        writer="github-actions",
        sources=list(sources),
    )


def v1_document() -> dict:
    """Builds a manifest document as schema v1 wrote it, before completeness existed.

    Returns:
        dict: Parsed manifest.json with schema_version 1 and no v2 fields.
    """
    data = json.loads(manifest(snapshot(fields_present=None, checks=None, findings=None)).to_json())
    data["schema_version"] = 1
    for source in data["sources"]:
        for name in ("fields_present", "checks", "findings"):
            source.pop(name)
    return data


def test_new_run_manifest_is_written_as_schema_version_2():
    # Sample: a manifest built today. Readers must be able to tell it carries completeness.
    assert json.loads(manifest(snapshot()).to_json())["schema_version"] == 2


def test_v2_manifest_with_completeness_checks_and_findings_round_trips_through_json():
    # Sample: 3 records, three fields (one always empty), two checks, one pre-MDA finding.
    original = manifest(snapshot())
    assert RunManifest.from_dict(json.loads(original.to_json())) == original


def test_v1_manifest_still_loads_with_completeness_fields_as_none():
    # Sample: a v1 manifest as the store holds today. The next weekly run loads it through
    # latest.json to diff against, so rejecting v1 would fail the first run after deploy.
    [source] = RunManifest.from_dict(v1_document()).sources
    assert (source.fields_present, source.checks, source.findings) == (None, None, None)


def test_v1_manifest_re_serializes_as_v1_and_never_claims_checks():
    # Sample: the v1 document loaded and written back. Upgrading it to v2 would make "never
    # checked" look like "checked, nothing found".
    data = json.loads(RunManifest.from_dict(v1_document()).to_json())
    assert data["schema_version"] == 1
    assert "checks" not in data["sources"][0] and "findings" not in data["sources"][0]


def test_v1_document_carrying_v2_fields_is_rejected():
    # Sample: a v1 document with a findings list. A v1 writer could not have run checks, so
    # the version or the content is wrong.
    data = v1_document()
    data["sources"][0]["findings"] = [PRE_MDA]
    with pytest.raises(ValueError, match="schema_version 1"):
        RunManifest.from_dict(data)


@pytest.mark.parametrize("version", [3, None, "2"], ids=["future", "missing", "string"])
def test_manifest_with_an_unsupported_schema_version_is_rejected(version):
    # Sample: schema_version 3 (a newer writer), absent, or the string "2".
    data = json.loads(manifest(snapshot()).to_json())
    data["schema_version"] = version
    with pytest.raises(ValueError, match="schema_version"):
        RunManifest.from_dict(data)


def test_source_checked_with_no_findings_differs_from_a_source_never_checked():
    # Sample: one snapshot with checks and findings [], one with both None. Only the first
    # certifies a clean result.
    clean = snapshot(findings=[])
    unchecked = snapshot(fields_present=None, checks=None, findings=None)
    assert clean.findings == [] and unchecked.findings is None
    assert RunManifest.from_dict(json.loads(manifest(clean).to_json())).sources[0].findings == []


def test_fields_present_serializes_in_path_order():
    # Sample: fields given as /z then /a. The same run must always write the same bytes.
    source = snapshot(fields_present={"/z": 1, "/a": 2})
    assert list(source.fields_present) == ["/a", "/z"]


@pytest.mark.parametrize("count", [4, -1], ids=["above-records", "negative"])
def test_fields_present_rejects_a_count_outside_zero_to_the_record_total(count):
    # Sample: 3 records with /k_number present in 4 or -1 of them.
    with pytest.raises(ValueError, match="fields_present"):
        snapshot(fields_present={"/k_number": count})


def test_fields_present_rejects_a_path_that_is_not_a_json_pointer():
    # Sample: "k_number" without the leading "/".
    with pytest.raises(ValueError, match="fields_present"):
        snapshot(fields_present={"k_number": 3})


def test_fields_present_rejects_a_bool_count():
    # Sample: True as a count. bool is an int subclass and would pass a plain int check.
    with pytest.raises(ValueError, match="fields_present"):
        snapshot(fields_present={"/k_number": True})


def test_checks_reject_the_same_check_id_listed_twice():
    # Sample: ["export_counts.total", "export_counts.total"].
    with pytest.raises(ValueError, match="checks"):
        snapshot(checks=["export_counts.total", "export_counts.total"], findings=[])


def test_findings_require_the_checks_that_produced_them():
    # Sample: findings given but checks None. A finding without its check list can't show
    # what else was verified.
    with pytest.raises(ValueError, match="checks"):
        snapshot(checks=None)


def test_finding_from_a_check_that_did_not_run_is_rejected():
    # Sample: a dates.decision_before_mda finding while checks lists only export_counts.total.
    with pytest.raises(ValueError, match="dates.decision_before_mda"):
        snapshot(checks=["export_counts.total"])


def test_fail_finding_in_a_written_manifest_is_rejected():
    # Sample: the pre-MDA finding with severity "fail". A fail finding stops the run before a
    # manifest exists, so one recorded here means a gate was bypassed.
    with pytest.raises(ValueError, match="fail"):
        snapshot(findings=[rm.ManifestFinding(**{**PRE_MDA, "severity": "fail"})])


@pytest.mark.parametrize(
    "override",
    [{"severity": "warn"}, {"register_ids": ["R1"]}, {"register_ids": ["R14", "R14"]}, {"check": "Dates"}],
    ids=["severity", "register-format", "register-duplicate", "check-id"],
)
def test_manifest_finding_rejects_malformed_fields(override):
    # Sample: the pre-MDA finding with one field broken. The manifest is the durable record,
    # so it validates findings itself instead of trusting the writer.
    with pytest.raises(ValueError):
        rm.ManifestFinding(**{**PRE_MDA, **override})


def test_manifest_finding_accepts_what_a_pipeline_finding_serializes():
    # Sample: a pipeline Finding's to_dict for the pre-MDA rule. The two types live in
    # different packages (core can't import pipeline), so their shapes must stay in step.
    from recension.pipeline.crawler.findings import CheckSpec

    data = CheckSpec("dates.decision_before_mda", "report", ("R14",)).finding("510k", PRE_MDA["message"]).to_dict()
    assert rm.ManifestFinding(**data).to_dict() == data
