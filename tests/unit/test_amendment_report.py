"""Unit tests for the openFDA amendment and removal report (#91, ADR-021).

Task map:
    Input: a raw store holding run manifests under runs/<run_id>/manifest.json, and the change
        lists their diffs link (runs/<run_id>/diffs/<source>.jsonl.gz).
    Output: one row per run and diffed source. It splits amended keys into FDA edits (a
        substantive change outside openfda), openfda re-enrichment only, and cosmetic only. It
        counts FDA edits per field and per group (class, dates, identifiers, status, decision),
        and records each removed key with the run that first missed it and any run where it
        returned. It also gives the run each source's point-in-time history starts from.
    Edge cases: an empty store; a source's first snapshot (no diff, so no row); a counts-only
        v1/v2 diff (no split, unclassified); a v3 change list (openfda split, no cosmetic
        classification); a v4 diff with no changes (a classified row of zeros); a key removed,
        returned and removed again; runs whose run ids sort differently from their start times;
        a change list whose bytes no longer match its manifest; a malformed manifest; change
        lists and other objects under runs/ that are not manifests; a week where openFDA
        published no new export (status unchanged), which must not read as a zero-change week.
    Invariants: only point-in-time data is reported, never reconstructed history; the store is
        only read.
New names are reached through a function so this file imports on the base branch (#137).
"""

import hashlib
import importlib
from collections import Counter

import pytest
from filiation.core.schemas import run_manifest as rm
from filiation.core.schemas.run_manifest import (
    ManifestObject,
    RunManifest,
    SourceDiff,
    SourceSnapshot,
)
from filiation.core.storage.raw_store import LocalRawStore
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError

SHA = "eb479de53bcb8b48c73c9a5ecaf6eea75eb84a00431ac0c04000090005c663c9"
COMMIT = "f7d257a" + "0" * 33


def report_module():
    """Imports the report module, which the base branch doesn't have.

    Returns:
        module: filiation.pipeline.crawler.amendment_report.
    """
    return importlib.import_module("filiation.pipeline.crawler.amendment_report")


def amended(key: str, fields: tuple[str, ...], cosmetic: tuple[str, ...] = ()):
    """Builds one amended KeyChange.

    Args:
        key: Record key.
        fields: Changed top-level fields.
        cosmetic: The subset of fields that changed only cosmetically.

    Returns:
        KeyChange: The change.
    """
    return rm.KeyChange(key, "amended", fields, cosmetic=cosmetic)


def put_run(store, run_id: str, started_at: str, sources: dict, version: int = 4, unchanged: tuple[str, ...] = ()) -> None:
    """Stores one run's manifest, and its change lists when its version links them.

    Args:
        store: Raw store to write into.
        run_id: The run id; it starts with the run date.
        started_at: The run's UTC start time.
        sources: Source name to its changes: None for a first snapshot (no diff), or a list
            of KeyChange (empty for a diff with no changes).
        version: Manifest schema_version; below 3 the diff carries counts only.
        unchanged: Sources whose export date didn't move, so the run reused the stored export.
    """
    snapshots = []
    for name, changes in sources.items():
        diff = None
        if changes is not None:
            link = None
            if changes and version >= 3:
                data = rm.encode_changes(changes)
                key = rm.changes_key(run_id, name)
                store.put_once(key, data)
                link = ManifestObject(key, hashlib.sha256(data).hexdigest(), len(data), len(changes))
            kinds = Counter(c.change for c in changes)
            fields = Counter(f for c in changes for f in c.fields)
            diff = SourceDiff("2026-11-01_0", "2026-11-01", kinds["added"], kinds["removed"], kinds["amended"], dict(fields), link)
        export = ManifestObject(f"openfda/{name}/export_date=2026-11-01/device-{name}-0001-of-0001.json.zip", SHA, 10, 1)
        status = "unchanged" if name in unchanged else "stored"
        snapshots.append(SourceSnapshot(name, status, [export], "2026-11-01", diff=diff))
    manifest = RunManifest(
        run_id=run_id,
        run_date=run_id[:10],
        started_at=started_at,
        finished_at=started_at,
        commit_sha=COMMIT,
        writer="github-actions",
        sources=snapshots,
        schema_version=version,
    )
    store.put_once(manifest.manifest_key, manifest.to_json())


def build(tmp_path, *runs):
    """Stores the given runs and builds the report over them.

    Args:
        tmp_path: Directory for the store and downloads.
        *runs: Argument tuples for put_run, after the store.

    Returns:
        AmendmentReport: The report.
    """
    store = LocalRawStore(tmp_path / "store")
    for run in runs:
        put_run(store, *run)
    return report_module().amendment_report(store, work_dir=tmp_path)


def test_report_on_an_empty_store_has_no_rows_and_says_so(tmp_path):
    # Sample: a store no ingestion run has written to yet.
    report = build(tmp_path)
    assert report.rows == [] and report.removals == []
    assert "No runs yet" in report_module().report_markdown(report)


def test_report_starts_each_source_at_its_first_snapshot_and_gives_it_no_row(tmp_path):
    # Sample: run 1 is the recall source's first snapshot, so there is nothing to diff it against.
    report = build(tmp_path, ("2026-11-16_1", "2026-11-16T19:00:00Z", {"recall": None}))
    assert report.rows == []
    assert report.since == {"recall": "2026-11-16_1"}


def test_report_splits_amended_keys_into_fda_edits_openfda_enrichment_and_cosmetic(tmp_path):
    # Sample: four amended recalls, shaped like the real Nov 13 -> 18 diff.
    #   Z-0774-03: description blanked to "n/a" (FDA edit)
    #   Z-0636-2013: openfda re-enriched, action whitespace only (openfda only)
    #   Z-0001-2026: openfda list reordered only (cosmetic)
    #   Z-0002-2026: action whitespace only (cosmetic)
    changes = [
        amended("Z-0001-2026", ("openfda",), ("openfda",)),
        amended("Z-0002-2026", ("action",), ("action",)),
        amended("Z-0636-2013", ("action", "openfda"), ("action",)),
        amended("Z-0774-03", ("code_info", "openfda", "product_description")),
    ]
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"recall": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"recall": changes}),
    )
    (row,) = report.rows
    assert (row.run_id, row.source, row.classified) == ("2026-11-23_2", "recall", True)
    assert (row.amended, row.fda_edits, row.openfda_only, row.cosmetic_only) == (4, 1, 1, 2)
    assert row.fda_fields == {"code_info": 1, "product_description": 1}


def test_report_counts_fda_edits_by_group(tmp_path):
    # Sample: enforcement Z-2372-2023 reclassified and given a termination date, Z-2373-2023
    # terminated; the diff key is recall_number. Groups count keys, not field changes.
    changes = [
        amended("Z-2372-2023", ("classification", "status", "termination_date")),
        amended("Z-2373-2023", ("status",)),
    ]
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"enforcement": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"enforcement": changes}),
    )
    (row,) = report.rows
    assert row.groups == {"class": 1, "dates": 1, "status": 2}


def test_field_groups_cover_the_categories_the_issue_names_for_each_source():
    # Sample: the FIELD_GROUPS table. #91 asks for class, dates, K-numbers (identifiers) and
    # status; the recall export carries no class, and 510(k) records have a decision, not a status.
    groups = report_module().FIELD_GROUPS
    assert groups["recall"]["identifiers"] >= {"k_numbers", "pma_numbers"}
    assert groups["recall"]["status"] == {"recall_status"}
    assert groups["enforcement"]["class"] == {"classification"}
    assert groups["510k"]["dates"] >= {"decision_date", "date_received"}


def test_report_lists_a_counts_only_v2_diff_as_unclassified_with_no_split(tmp_path):
    # Sample: the real 2026-11-18 run, written at v2: counts only, no change list.
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"510k": None}, 2),
        ("2026-11-18_2", "2026-11-18T16:00:00Z", {"510k": [amended("K210758", ("openfda",))]}, 2),
    )
    (row,) = report.rows
    assert (row.classified, row.amended, row.fda_edits, row.openfda_only, row.cosmetic_only) == (False, 1, None, None, None)


def test_report_separates_openfda_in_a_v3_list_but_does_not_call_it_classified(tmp_path):
    # Sample: a v3 change list. It names changed fields but never marked cosmetic ones, so a
    # whitespace-only FDA field still counts as an FDA edit there, and the row says so.
    changes = [amended("K210758", ("openfda",)), amended("K030123", ("contact", "openfda"))]
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"510k": None}, 3),
        ("2026-11-20_2", "2026-11-20T05:00:00Z", {"510k": changes}, 3),
    )
    (row,) = report.rows
    assert (row.classified, row.fda_edits, row.openfda_only, row.cosmetic_only) == (False, 1, 1, None)


def test_report_lists_a_v4_diff_with_no_changes_as_a_classified_row_of_zeros(tmp_path):
    # Sample: the real 2026-11-20 enforcement diff: a new export date with identical records.
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"enforcement": None}),
        ("2026-11-20_2", "2026-11-20T05:00:00Z", {"enforcement": []}),
    )
    (row,) = report.rows
    assert (row.classified, row.added, row.removed, row.amended, row.fda_edits) == (True, 0, 0, 0, 0)


def test_report_records_the_run_that_first_missed_a_removed_key_and_the_run_it_returned_in(tmp_path):
    # Sample: recall Z-0500-2020 vanishes in run 2 and reappears in run 3; Z-0600-2021 vanishes
    # in run 3 and stays gone. History is kept: the report names both runs.
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"recall": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"recall": [rm.KeyChange("Z-0500-2020", "removed")]}),
        (
            "2026-11-30_3",
            "2026-11-30T06:00:00Z",
            {"recall": [rm.KeyChange("Z-0500-2020", "added"), rm.KeyChange("Z-0600-2021", "removed")]},
        ),
    )
    removals = [(r.source, r.key, r.first_missed_run, r.returned_run) for r in report.removals]
    assert removals == [
        ("recall", "Z-0500-2020", "2026-11-23_2", "2026-11-30_3"),
        ("recall", "Z-0600-2021", "2026-11-30_3", None),
    ]


def test_report_counts_a_key_removed_again_after_returning_as_a_new_removal(tmp_path):
    # Sample: K210758 removed in run 2, back in run 3, removed again in run 4.
    removed, added = rm.KeyChange("K210758", "removed"), rm.KeyChange("K210758", "added")
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"510k": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"510k": [removed]}),
        ("2026-11-30_3", "2026-11-30T06:00:00Z", {"510k": [added]}),
        ("2026-12-07_4", "2026-12-07T06:00:00Z", {"510k": [removed]}),
    )
    assert [(r.first_missed_run, r.returned_run) for r in report.removals] == [
        ("2026-11-23_2", "2026-11-30_3"),
        ("2026-12-07_4", None),
    ]


def test_report_orders_runs_by_start_time_not_by_run_id(tmp_path):
    # Sample: two runs on one day, ids 9 and 10. As strings "10" sorts before "9", but run 9
    # started first, so it is the source's first snapshot and run 10 is diffed against it.
    report = build(
        tmp_path,
        ("2026-11-23_10", "2026-11-23T07:00:00Z", {"recall": [rm.KeyChange("Z-0600-2021", "removed")]}),
        ("2026-11-23_9", "2026-11-23T06:00:00Z", {"recall": None}),
    )
    assert report.since == {"recall": "2026-11-23_9"}
    assert [row.run_id for row in report.rows] == ["2026-11-23_10"]


def test_report_rejects_a_change_list_whose_bytes_no_longer_match_its_manifest(tmp_path):
    # Sample: a v4 run whose stored list was replaced after the manifest recorded its hash.
    store = LocalRawStore(tmp_path / "store")
    put_run(store, "2026-11-23_2", "2026-11-23T06:00:00Z", {"recall": [rm.KeyChange("Z-0600-2021", "removed")]})
    path = tmp_path / "store" / rm.changes_key("2026-11-23_2", "recall")
    path.chmod(0o644)
    path.write_bytes(rm.encode_changes([rm.KeyChange("Z-0700-2022", "removed")]))
    with pytest.raises(ExportIntegrityError):
        report_module().amendment_report(store, work_dir=tmp_path)


def test_report_rejects_a_malformed_manifest_instead_of_skipping_it(tmp_path):
    # Sample: a truncated manifest.json. Skipping it would silently drop a week from the rates.
    store = LocalRawStore(tmp_path / "store")
    store.put_once("runs/2026-11-23_2/manifest.json", b'{"schema_version": 4, "run_id": ')
    with pytest.raises(ValueError):
        report_module().amendment_report(store, work_dir=tmp_path)


def test_markdown_labels_the_rows_point_in_time_since_each_source_first_snapshot(tmp_path):
    # Sample: one recall diff after the first snapshot. The summary must say where point-in-time
    # history starts, and that anything earlier is reconstructed (#91 criterion 4).
    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"recall": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"recall": [amended("Z-0774-03", ("product_description",))]}),
    )
    text = report_module().report_markdown(report)
    assert "point-in-time since 2026-11-16_1" in text
    assert "reconstructed" in text
    assert "| 2026-11-23_2 | recall | new |" in text


def test_report_to_dict_round_trips_through_json(tmp_path):
    # Sample: the same one-row report, for the --format json output.
    import json

    report = build(
        tmp_path,
        ("2026-11-16_1", "2026-11-16T19:00:00Z", {"recall": None}),
        ("2026-11-23_2", "2026-11-23T06:00:00Z", {"recall": [rm.KeyChange("Z-0600-2021", "removed")]}),
    )
    data = json.loads(json.dumps(report.to_dict()))
    assert data["since"] == {"recall": "2026-11-16_1"}
    assert data["rows"][0]["removed"] == 1
    assert data["removals"] == [{"source": "recall", "key": "Z-0600-2021", "first_missed_run": "2026-11-23_2", "returned_run": None}]


def test_report_row_says_whether_openfda_published_a_new_export_that_week(tmp_path):
    # Sample: the real 2026-11-20 run, where 510(k) and recall kept their Nov 18 export dates
    # (status unchanged) and enforcement had a new one. Only the new export is a week of data.
    report = build(
        tmp_path,
        ("2026-11-18_1", "2026-11-18T16:00:00Z", {"510k": None, "enforcement": None}),
        ("2026-11-20_2", "2026-11-20T05:00:00Z", {"510k": [], "enforcement": []}, 4, ("510k",)),
    )
    assert [(row.source, row.export_status) for row in report.rows] == [("510k", "unchanged"), ("enforcement", "stored")]
