"""Unit tests for the run manifest contract in stemma.core.schemas.run_manifest (ADR-010, #42).

Task map:
    Input: the fields of one ingestion run: run id and date, timestamps, commit SHA, writer,
        and per-source objects (key, SHA-256, size, record count).
    Output: a RunManifest that serializes to JSON and back unchanged, and a LatestPointer
        naming the newest manifest.
    Edge cases: malformed SHA-256 or commit SHA; unknown writer or status; a run_id that
        doesn't start with its run_date; a non-ISO export_date; negative sizes.
    Invariants: manifest_key is always runs/<run_id>/manifest.json; a manifest that loads
        from JSON equals the one that wrote it; invalid manifests fail at construction.

SourceDiff (#40):
    Input: one source's added/removed/amended counts against the previous run, that run's
        id and export_date, and how many amended records changed each field.
    Output: a diff stored on the SourceSnapshot that round-trips through JSON.
    Edge cases: negative counts; amendments with no changed fields (or the reverse); a
        field count above the amended total; manifests written before diffs existed.
    Invariants: changed_fields serializes in field-name order, so the same diff always
        produces the same JSON.
"""

import json

import pytest
from stemma.core.schemas.run_manifest import (
    LatestPointer,
    ManifestObject,
    RunManifest,
    SourceDiff,
    SourceSnapshot,
)

SHA = "eb479de53bcb8b48c73c9a5ecaf6eea75eb84a00431ac0c04000090005c663c9"
COMMIT = "f7d257a" + "0" * 33


def make_manifest(**overrides) -> RunManifest:
    """Builds a valid one-source manifest modeled on the 2026-11-09 510(k) export.

    Args:
        **overrides: RunManifest fields to replace.

    Returns:
        RunManifest: The manifest.
    """
    fields = {
        "run_id": "2026-11-16_43306392741",
        "run_date": "2026-11-16",
        "started_at": "2026-11-16T06:00:04Z",
        "finished_at": "2026-11-16T06:01:10Z",
        "commit_sha": COMMIT,
        "writer": "github-actions",
        "sources": [
            SourceSnapshot(
                source="510k",
                export_date="2026-11-09",
                last_updated="2026-11-09",
                status="unchanged",
                objects=[
                    ManifestObject(
                        key="openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip",
                        sha256=SHA,
                        size_bytes=249963096,
                        records=176625,
                    )
                ],
            )
        ],
    }
    fields.update(overrides)
    return RunManifest(**fields)


def test_run_manifest_round_trips_through_json_unchanged():
    # Sample: the manifest posted for Alden's review on #42 (real 2026-11-09 export SHA,
    # size, and record count). JSON out and back in must give an equal manifest.
    manifest = make_manifest()
    assert RunManifest.from_dict(json.loads(manifest.to_json())) == manifest


def test_run_manifest_json_carries_schema_version_1():
    # Sample: default manifest. The version lets the loader (#54) refuse formats it
    # doesn't understand.
    assert json.loads(make_manifest().to_json())["schema_version"] == 1


def test_run_manifest_key_is_under_runs_and_run_id():
    # Sample: run_id 2026-11-16_43306392741. ADR-010 layout: runs/<date>_<run-id>/manifest.json.
    assert make_manifest().manifest_key == "runs/2026-11-16_43306392741/manifest.json"


@pytest.mark.parametrize(
    "overrides",
    [
        {"commit_sha": "f7d257a"},
        {"writer": "laptop"},
        {"run_id": "2026-11-15_43306392741"},
        {"run_id": "2026-11-16_bad id"},
        {"run_date": "11/16/2026", "run_id": "11/16/2026_1"},
    ],
    ids=["short-commit", "unknown-writer", "run-id-date-mismatch", "run-id-space", "non-iso-run-date"],
)
def test_run_manifest_rejects_invalid_run_fields(overrides):
    # Sample: one bad field each. A short SHA can't pin code; "laptop" isn't a writer
    # ADR-010 allows; run_id must start with run_date and be key-safe.
    with pytest.raises(ValueError):
        make_manifest(**overrides)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sha256": "abc"},
        {"sha256": SHA.upper()},
        {"size_bytes": -1},
        {"records": -5},
    ],
    ids=["short-sha", "uppercase-sha", "negative-size", "negative-records"],
)
def test_manifest_object_rejects_invalid_hash_size_or_count(kwargs):
    # Sample: one bad field each on an otherwise valid object. SHA-256 must be 64
    # lowercase hex characters so it compares byte-for-byte with hashlib's hexdigest.
    fields = {"key": "openfda/510k/x.zip", "sha256": SHA, "size_bytes": 1, "records": 1}
    fields.update(kwargs)
    with pytest.raises(ValueError):
        ManifestObject(**fields)


@pytest.mark.parametrize(
    "kwargs",
    [{"status": "skipped"}, {"export_date": "2026-13-01"}, {"last_updated": "yesterday"}],
    ids=["unknown-status", "invalid-export-date", "invalid-last-updated"],
)
def test_source_snapshot_rejects_unknown_status_or_bad_export_date(kwargs):
    # Sample: status "skipped" (only stored/unchanged exist) and month 13.
    fields = {"source": "510k", "export_date": "2026-11-09", "status": "stored", "objects": []}
    fields.update(kwargs)
    with pytest.raises(ValueError):
        SourceSnapshot(**fields)


def test_latest_pointer_round_trips_and_names_the_manifest():
    # Sample: pointer to the 2026-11-16 run's manifest.
    pointer = LatestPointer.for_manifest(make_manifest())
    loaded = LatestPointer.from_dict(json.loads(pointer.to_json()))
    assert loaded == pointer
    assert loaded.manifest_key == "runs/2026-11-16_43306392741/manifest.json"
    assert loaded.run_id == "2026-11-16_43306392741"
    assert loaded.updated_at == "2026-11-16T06:01:10Z"


def make_diff(**overrides) -> SourceDiff:
    """Builds a valid diff: 12 added, 1 removed, 3 amended against last week's 510(k) run.

    Args:
        **overrides: SourceDiff fields to replace.

    Returns:
        SourceDiff: The diff.
    """
    fields = {
        "previous_run_id": "2026-11-09_43306392000",
        "previous_export_date": "2026-11-02",
        "added": 12,
        "removed": 1,
        "amended": 3,
        "changed_fields": {"openfda": 3, "decision_date": 1},
    }
    fields.update(overrides)
    return SourceDiff(**fields)


def test_run_manifest_round_trips_a_source_diff_through_json():
    # Sample: the default 510(k) manifest with a diff attached (12 added, 1 removed,
    # 3 amended). The diff must survive JSON out and back in unchanged.
    manifest = make_manifest()
    snapshot = manifest.sources[0]
    with_diff = make_manifest(
        sources=[SourceSnapshot(snapshot.source, snapshot.status, snapshot.objects, snapshot.export_date,
                                snapshot.last_updated, diff=make_diff())]
    )
    assert RunManifest.from_dict(json.loads(with_diff.to_json())) == with_diff


def test_run_manifest_loads_a_manifest_written_before_diffs_existed():
    # Sample: the default manifest's JSON with no "diff" key on its source, as written by
    # runs before #40. It must still load, with diff None.
    data = json.loads(make_manifest().to_json())
    data["sources"][0].pop("diff", None)
    assert RunManifest.from_dict(data).sources[0].diff is None


def test_source_diff_serializes_changed_fields_in_field_name_order():
    # Sample: changed_fields given as {"openfda", "decision_date"}. JSON must list
    # decision_date first so reruns over the same exports give byte-identical manifests.
    data = make_diff().to_dict()
    assert list(data["changed_fields"]) == ["decision_date", "openfda"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"added": -1},
        {"removed": -2},
        {"amended": 0},
        {"amended": 2, "changed_fields": {}},
        {"amended": 3, "changed_fields": {"openfda": 4}},
        {"changed_fields": {"openfda": 0, "decision_date": 3}},
        {"previous_export_date": "2026/11/02"},
    ],
    ids=[
        "negative-added",
        "negative-removed",
        "fields-without-amendments",
        "amendments-without-fields",
        "field-count-above-amended",
        "zero-field-count",
        "non-iso-previous-export-date",
    ],
)
def test_source_diff_rejects_inconsistent_counts(overrides):
    # Sample: one inconsistency each on the default diff. Every amended record changed at
    # least one field, and no field can change in more records than were amended.
    with pytest.raises(ValueError):
        make_diff(**overrides)
