"""Unit tests for run manifest schema v3: per-key change lists linked from each source's diff (#76, ADR-018).

Task map:
    Input: per-key changes between two exports of one source (KeyChange: key, added/removed/
        amended, and the top-level fields an amended record changed), and SourceDiffs that
        link the stored change list as a ManifestObject.
    Output: a gzip JSON Lines change list whose bytes depend only on the changes, that reads
        back to the same changes; v3 manifests whose diffs link it; v1 and v2 manifests that
        still load and re-serialize without it.
    Edge cases: changes given in any order; a non-ASCII key; an amended change without fields,
        or an added/removed one with fields; an unknown change kind; a duplicate key; a stored
        list that is out of order, truncated, or not gzip; an empty list; a diff whose linked
        list counts a different number of records than the diff; a list stored outside the
        run's own diffs/ key; a v3 diff with changes but no list, or no changes but a list;
        a v2 document carrying a list; a v2 manifest written back; schema_version 5.
    Invariants: new manifests are v3 or later; the same changes always encode to the same bytes, so a
        rerun on the same pair of exports writes an identical object.
New names are reached through the module so this file imports on the base branch (#137).
"""

import gzip
import json

import pytest
from filiation.core.schemas import run_manifest as rm
from filiation.core.schemas.run_manifest import (
    ManifestObject,
    RunManifest,
    SourceDiff,
    SourceSnapshot,
)

SHA = "eb479de53bcb8b48c73c9a5ecaf6eea75eb84a00431ac0c04000090005c663c9"
CHANGES_SHA = "0" * 63 + "1"
COMMIT = "f7d257a" + "0" * 33
KEY = "openfda/510k/export_date=2026-11-16/device-510k-0001-of-0001.json.zip"
CHANGES_KEY = "runs/2026-11-23_102/diffs/510k.jsonl.gz"


def change(key: str, kind: str = "amended", fields: tuple[str, ...] = ("openfda",)):
    """Builds one KeyChange; added and removed changes carry no fields.

    Args:
        key: Record key, e.g. a K-number.
        kind: "added", "removed", or "amended".
        fields: Top-level fields an amended record changed.

    Returns:
        KeyChange: The change.
    """
    return rm.KeyChange(key, kind, fields if kind == "amended" else ())


def diff(records: int | None = 3, **overrides) -> SourceDiff:
    """Builds a 1-added, 1-removed, 1-amended diff against run 101, linking a change list.

    Args:
        records: Record count of the linked change list; None links no list.
        **overrides: SourceDiff fields to replace.

    Returns:
        SourceDiff: The diff.
    """
    fields = {
        "previous_run_id": "2026-11-16_101",
        "previous_export_date": "2026-11-09",
        "added": 1,
        "removed": 1,
        "amended": 1,
        "changed_fields": {"openfda": 1},
        "changes": ManifestObject(CHANGES_KEY, CHANGES_SHA, 120, records) if records is not None else None,
    }
    fields.update(overrides)
    return SourceDiff(**fields)


def manifest(source_diff: SourceDiff | None, version: int | None = None) -> RunManifest:
    """Builds run 102's manifest with one 510(k) source carrying the given diff.

    Args:
        source_diff: The 510(k) diff.
        version: schema_version to set; None takes the default.

    Returns:
        RunManifest: The manifest.
    """
    extra = {} if version is None else {"schema_version": version}
    source = SourceSnapshot("510k", "stored", [ManifestObject(KEY, SHA, 1000, 3)], "2026-11-16", diff=source_diff)
    return RunManifest(
        run_id="2026-11-23_102",
        run_date="2026-11-23",
        started_at="2026-11-23T06:00:00Z",
        finished_at="2026-11-23T06:01:00Z",
        commit_sha=COMMIT,
        writer="github-actions",
        sources=[source],
        **extra,
    )


def test_change_list_encodes_one_json_line_per_key_in_key_order():
    # Sample: three changes given out of key order; K882211 was removed, K251234 added,
    # K210758 amended in decision_date and openfda. Lines must come out sorted by key.
    data = rm.encode_changes(
        [change("K882211", "removed"), change("K251234", "added"), change("K210758", fields=("openfda", "decision_date"))]
    )
    lines = [json.loads(line) for line in gzip.decompress(data).decode("utf-8").splitlines()]
    assert lines == [
        {"key": "K210758", "change": "amended", "fields": ["decision_date", "openfda"]},
        {"key": "K251234", "change": "added"},
        {"key": "K882211", "change": "removed"},
    ]


def test_change_list_bytes_depend_only_on_the_changes():
    # Sample: the same two changes in two orders. Equal bytes make a rerun on the same pair of
    # exports write an identical object, and the gzip header carries no timestamp or name.
    first = rm.encode_changes([change("K1", "added"), change("K2", "removed")])
    second = rm.encode_changes([change("K2", "removed"), change("K1", "added")])
    assert first == second
    assert first[4:8] == b"\x00\x00\x00\x00"
    assert first[3] & 0x08 == 0


def test_change_list_reads_back_to_the_same_changes():
    # Sample: an added, a removed and an amended change, including a non-ASCII key
    # ("event_id=9452é", the enforcement fallback-key shape) that must survive UTF-8.
    changes = [change("K1", "added"), change("event_id=9452é", "removed"), change("K2", fields=("openfda",))]
    assert rm.decode_changes(rm.encode_changes(changes)) == sorted(changes, key=lambda c: c.key)


def test_empty_change_list_round_trips():
    # Sample: no changes at all. It still encodes and decodes, to an empty list.
    assert rm.decode_changes(rm.encode_changes([])) == []


@pytest.mark.parametrize(
    "args",
    [("K1", "amended", ()), ("K1", "added", ("openfda",)), ("K1", "renamed", ()), ("", "added", ())],
    ids=["amended-without-fields", "added-with-fields", "unknown-kind", "blank-key"],
)
def test_key_change_rejects_inconsistent_fields_an_unknown_kind_or_a_blank_key(args):
    # Sample: an amendment naming no field, an addition naming one, a kind the diff never
    # produces, and a blank key; each would make the list say something the diff did not.
    with pytest.raises(ValueError):
        rm.KeyChange(*args)


def test_change_list_rejects_the_same_key_twice():
    # Sample: K1 listed as added and as amended; one key has exactly one change per diff.
    with pytest.raises(ValueError, match="K1"):
        rm.encode_changes([change("K1", "added"), change("K1")])


@pytest.mark.parametrize(
    "data",
    [
        gzip.compress(b'{"key":"K2","change":"added"}\n{"key":"K1","change":"added"}\n'),
        gzip.compress(b'{"key":"K1","change":"added"}\n{"key":"K2","chan'),
        b'{"key":"K1","change":"added"}\n',
    ],
    ids=["out-of-order", "truncated", "not-gzip"],
)
def test_decode_changes_rejects_a_list_out_of_order_truncated_or_not_gzip(data):
    # Sample: a stored list whose keys are not sorted, one cut off mid-line, and one never
    # compressed. A reader must fail loudly rather than return part of a list.
    with pytest.raises(ValueError):
        rm.decode_changes(data)


def test_new_run_manifest_is_written_at_a_version_whose_diffs_link_change_lists():
    # Sample: a manifest built today. Readers must be able to tell its diffs link change lists,
    # which every version from v3 does; v4 (ADR-021) also marks cosmetic fields.
    version = json.loads(manifest(diff()).to_json())["schema_version"]
    assert version == rm.SCHEMA_VERSION
    assert version >= rm.DIFF_FIELDS_SINCE["changes"]


def test_v3_manifest_with_a_linked_change_list_round_trips_through_json():
    # Sample: run 102 with a 1/1/1 diff linking runs/2026-11-23_102/diffs/510k.jsonl.gz.
    original = manifest(diff())
    assert RunManifest.from_dict(json.loads(original.to_json())) == original


def test_diff_whose_change_list_counts_other_records_is_rejected():
    # Sample: a 1/1/1 diff linking a list of 2 records; the list and the counts disagree.
    with pytest.raises(ValueError, match="3"):
        diff(records=2)


def test_change_list_stored_outside_the_runs_diffs_key_is_rejected():
    # Sample: run 102's diff linking run 101's list, and one linking a list named for recall.
    for key in ("runs/2026-11-16_101/diffs/510k.jsonl.gz", "runs/2026-11-23_102/diffs/recall.jsonl.gz"):
        linked = diff(changes=ManifestObject(key, CHANGES_SHA, 120, 3))
        with pytest.raises(ValueError, match="diffs"):
            manifest(linked)


def test_v3_diff_with_changes_but_no_change_list_is_rejected():
    # Sample: a v3 diff counting 3 changes with no list linked; incremental readers would
    # have nothing to reprocess from.
    with pytest.raises(ValueError, match="change list"):
        manifest(diff(records=None))


def test_diff_without_changes_links_no_change_list():
    # Sample: an unchanged export: 0/0/0. A zero diff links no list; linking one is rejected.
    zero = {"added": 0, "removed": 0, "amended": 0, "changed_fields": {}}
    assert manifest(diff(records=None, **zero)).sources[0].diff.changes is None
    with pytest.raises(ValueError):
        diff(records=0, **zero)


def v2_document() -> dict:
    """Builds a manifest document as schema v2 wrote it: a diff with counts and no change list.

    Returns:
        dict: Parsed manifest.json with schema_version 2.
    """
    data = json.loads(manifest(diff()).to_json())
    data["schema_version"] = 2
    data["sources"][0]["diff"].pop("changes")
    return data


def test_v2_manifest_still_loads_with_no_change_list():
    # Sample: a v2 manifest as the store holds today; the first v3 run diffs against it.
    [source] = RunManifest.from_dict(v2_document()).sources
    assert (source.diff.amended, source.diff.changes) == (1, None)


def test_v2_manifest_re_serializes_as_v2_without_a_change_list():
    # Sample: the v2 document loaded and written back must not gain a "changes" key.
    data = json.loads(RunManifest.from_dict(v2_document()).to_json())
    assert data["schema_version"] == 2 and "changes" not in data["sources"][0]["diff"]


def test_v2_document_carrying_a_change_list_is_rejected():
    # Sample: a v2 document whose diff links a list; a v2 writer could not have written one.
    data = v2_document()
    data["sources"][0]["diff"]["changes"] = {"key": CHANGES_KEY, "sha256": CHANGES_SHA, "size_bytes": 120, "records": 3}
    with pytest.raises(ValueError, match="schema_version 2"):
        RunManifest.from_dict(data)

