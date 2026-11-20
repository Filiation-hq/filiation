"""Unit tests for one ingestion run in filiation.pipeline.crawler.ingestion_run (#42).

Task map:
    Input: the sources to ingest, a RawStore, the run's id suffix, commit SHA, and writer,
        plus a fake openFDA opener and a fixed clock.
    Output: every source's export stored (or recognized as unchanged), one write-once
        manifest at runs/<date>_<id>/manifest.json listing every object, and latest.json
        pointing at it.
    Edge cases: a source that fails its record-count check; a second run with nothing new;
        a run id that was already used.
    Invariants: latest.json moves only after its manifest is written; a failed run leaves
        latest.json where it was; manifests are never overwritten.

Weekly diff (#40):
    Input: the manifest latest.json names when the run starts, and the objects both runs
        list for each source.
    Output: each source's SourceDiff (added/removed/amended against that manifest) in the
        new manifest; None when there is nothing to compare against.
    Edge cases: the first run (no latest.json); a source the previous run didn't ingest; an
        unchanged export (zero diff, nothing downloaded); a previous object whose bytes no
        longer match its manifest; latest.json naming a manifest that is gone; a diff that
        fails partway.
    Invariants: a run whose diff fails writes no manifest and leaves latest.json in place.

Per-key change lists (#76, ADR-018):
    Output: runs/<run_id>/diffs/<source>.jsonl.gz, linked from the source's diff with its
        SHA-256, size, and line count; no list when nothing changed or nothing to compare.
    Edge cases: an unchanged export; the first run; a rerun with the same run id after a crash
        left the list behind, with identical or different bytes.
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner
from filiation.core.schemas import run_manifest as rm
from filiation.core.schemas.run_manifest import RunManifest
from filiation.core.storage.raw_store import LATEST_KEY, KeyExistsError, LocalRawStore
from filiation.pipeline.crawler.ingestion_run import run_ingestion
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError
from openfda_fakes import (
    ENFORCEMENT_RECORDS,
    FIXTURES,
    KEY,
    enforcement_export_zip,
    export_body,
    fixture_export_zip,
    load_index,
    make_export_zip,
    opener_for,
)

COMMIT = "f7d257a" + "0" * 33
MANIFEST_KEY = "runs/2026-11-16_101/manifest.json"


def fixed_clock(*moments: str):
    """Returns a clock that yields the given UTC ISO times in order.

    Args:
        *moments: Times like "2026-11-16T06:00:04".

    Returns:
        Callable[[], datetime]: Successive timezone-aware datetimes.
    """
    times = iter(datetime.fromisoformat(m).replace(tzinfo=timezone.utc) for m in moments)
    return lambda: next(times)


def run(store, run_suffix="101", opener=None, clock=None):
    """Runs a 510(k)-only ingestion against fixtures with a fixed commit and clock.

    Args:
        store: RawStore to write into.
        run_suffix: Run id suffix, e.g. a GitHub run id.
        opener: Fake opener; defaults to the 3-record fixture.
        clock: Clock; defaults to 2026-11-16 06:00:04 then 06:01:10 UTC.

    Returns:
        RunManifest: The written manifest.
    """
    return run_ingestion(
        ["510k"],
        store,
        run_suffix=run_suffix,
        commit_sha=COMMIT,
        writer="github-actions",
        opener=opener or opener_for(fixture_export_zip()),
        clock=clock or fixed_clock("2026-11-16T06:00:04", "2026-11-16T06:01:10"),
    )


def test_run_ingestion_writes_manifest_listing_every_stored_object(tmp_path):
    # Sample: empty store, fixture 510(k) export (3 records, export_date 2026-11-09),
    # run suffix "101" on 2026-11-16. The manifest must record key, SHA-256, size, count,
    # export_date, commit, and run id.
    store = LocalRawStore(tmp_path)
    run(store)

    manifest = RunManifest.from_dict(json.loads((tmp_path / MANIFEST_KEY).read_text()))
    [snapshot] = manifest.sources
    [obj] = snapshot.objects
    assert (manifest.run_id, manifest.commit_sha, manifest.writer) == ("2026-11-16_101", COMMIT, "github-actions")
    assert (snapshot.source, snapshot.export_date, snapshot.status) == ("510k", "2026-11-09", "stored")
    assert snapshot.last_updated == "2026-11-09"
    assert (obj.key, obj.records) == (KEY, 3)
    assert obj.sha256 == hashlib.sha256(fixture_export_zip()).hexdigest()
    assert (manifest.started_at, manifest.finished_at) == ("2026-11-16T06:00:04Z", "2026-11-16T06:01:10Z")


def test_run_ingestion_points_latest_json_at_the_new_manifest(tmp_path):
    # Sample: one successful run. latest.json must name its manifest.
    store = LocalRawStore(tmp_path)
    run(store)
    assert json.loads((tmp_path / LATEST_KEY).read_text())["manifest_key"] == MANIFEST_KEY


def test_run_ingestion_leaves_latest_json_in_place_when_a_source_fails(tmp_path):
    # Sample: run 101 succeeds; run 102's index claims 4 records for a 3-record zip.
    # Run 102 must raise, write no manifest, and leave latest.json on run 101.
    store = LocalRawStore(tmp_path)
    run(store)
    index = load_index()
    index["results"]["device"]["510k"]["partitions"][0]["records"] = 4
    index["results"]["device"]["510k"]["total_records"] = 4
    with pytest.raises(ExportIntegrityError):
        run(store, run_suffix="102", opener=opener_for(fixture_export_zip(), index))

    assert not store.exists("runs/2026-11-16_102/manifest.json")
    assert json.loads((tmp_path / LATEST_KEY).read_text())["manifest_key"] == MANIFEST_KEY


def test_run_ingestion_second_run_lists_unchanged_objects_and_moves_latest(tmp_path):
    # Sample: two weekly runs (101, then 102 a week later) with the same openFDA export.
    # Run 102's manifest still lists the object, marked unchanged, so one manifest alone
    # describes the full snapshot.
    store = LocalRawStore(tmp_path)
    run(store)
    second = run(
        store,
        run_suffix="102",
        clock=fixed_clock("2026-11-23T06:00:00", "2026-11-23T06:00:30"),
    )

    assert second.sources[0].status == "unchanged"
    assert [o.key for o in second.sources[0].objects] == [KEY]
    pointer = json.loads((tmp_path / LATEST_KEY).read_text())
    assert pointer["manifest_key"] == "runs/2026-11-23_102/manifest.json"


def test_run_ingestion_refuses_to_reuse_a_run_id(tmp_path):
    # Sample: run 101 executed twice on the same day (e.g. a re-run of the same GitHub run
    # id). The manifest is write-once, so the second attempt must fail and leave the
    # first manifest and latest.json untouched.
    store = LocalRawStore(tmp_path)
    run(store)
    original = (tmp_path / MANIFEST_KEY).read_bytes()
    with pytest.raises(KeyExistsError):
        run(store, clock=fixed_clock("2026-11-16T07:00:00", "2026-11-16T07:00:30"))
    assert (tmp_path / MANIFEST_KEY).read_bytes() == original


def test_ingest_openfda_cli_writes_manifest_and_prints_it(tmp_path, monkeypatch):
    # Sample: `filiation-pipeline ingest-openfda --source 510k --run-id 101 --commit-sha <sha>
    # --writer local` with the store and opener patched to fixtures.
    import filiation.pipeline.cli as cli_module

    store = LocalRawStore(tmp_path)
    monkeypatch.setattr(cli_module, "raw_store_from_env", lambda: store)
    monkeypatch.setattr(cli_module, "default_opener", opener_for(fixture_export_zip()))

    result = CliRunner().invoke(
        cli_module.cli,
        ["ingest-openfda", "--source", "510k", "--run-id", "101", "--commit-sha", COMMIT, "--writer", "local"],
    )

    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["sources"][0]["objects"][0]["key"] == KEY
    assert store.exists(f"runs/{printed['run_id']}/manifest.json")
    assert json.loads((tmp_path / LATEST_KEY).read_text())["run_id"] == printed["run_id"]


def test_ingest_openfda_cli_includes_the_github_run_attempt_in_the_run_id(tmp_path, monkeypatch):
    # Sample: GITHUB_RUN_ID=101 and GITHUB_RUN_ATTEMPT=2, as when "Re-run jobs" repeats a
    # run. GitHub keeps the run id on re-runs, so without the attempt the manifest key
    # would collide with attempt 1's.
    import filiation.pipeline.cli as cli_module

    store = LocalRawStore(tmp_path)
    monkeypatch.setattr(cli_module, "raw_store_from_env", lambda: store)
    monkeypatch.setattr(cli_module, "default_opener", opener_for(fixture_export_zip()))
    env = {"GITHUB_RUN_ID": "101", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": COMMIT}

    result = CliRunner().invoke(cli_module.cli, ["ingest-openfda", "--source", "510k"], env=env)

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["run_id"].endswith("_101-2")


def next_week():
    """Returns a fresh clock for a run a week after run 101.

    Returns:
        Callable[[], datetime]: 2026-11-23 06:00:00, then 06:00:30 UTC.
    """
    return fixed_clock("2026-11-23T06:00:00", "2026-11-23T06:00:30")


def next_week_opener(records: list[dict]):
    """Serves a 510(k) export dated 2026-11-16 holding the given records.

    Args:
        records: Records for the new export's "results" array.

    Returns:
        FakeOpener: Opener whose index lists one partition with len(records) records.
    """
    index = load_index()
    entry = index["results"]["device"]["510k"]
    entry["export_date"] = "2026-11-16"
    entry["partitions"][0]["records"] = entry["total_records"] = len(records)
    body = export_body(records, last_updated="2026-11-16")
    return opener_for(make_export_zip({"device-510k-0001-of-0001.json": body}), index)


def fixture_records() -> list[dict]:
    """Loads the 3 records of the 510(k) fixture export.

    Returns:
        list[dict]: K210758, K030123, and K882211.
    """
    return json.loads((FIXTURES / "device-510k-0001-of-0001.json").read_text())["results"]


class CountingStore(LocalRawStore):
    """LocalRawStore that records every key fetched with get()."""

    def __init__(self, root):
        """Creates the store.

        Args:
            root: Directory holding the objects.
        """
        super().__init__(root)
        self.fetched: list[str] = []

    def get(self, key, dest):
        """Records key, then copies the object to dest.

        Args:
            key: Object key to fetch.
            dest: Local file path to write.

        Returns:
            Path: dest.
        """
        self.fetched.append(key)
        return super().get(key, dest)


def test_first_run_records_no_diff_because_there_is_no_previous_snapshot(tmp_path):
    # Sample: empty store, fixture export. With no latest.json there is nothing to diff
    # against, so the 510(k) snapshot's diff is None (a baseline), not "3 added".
    manifest = run(LocalRawStore(tmp_path))
    assert manifest.sources[0].diff is None


def test_run_diffs_each_source_against_the_snapshot_latest_json_named(tmp_path):
    # Sample: run 101 stores the 3-record fixture (export 2026-11-09). A week later openFDA
    # publishes export 2026-11-16: K882211 gone, K251234 new, K210758's decision_date
    # corrected. Run 102's manifest must record 1 added, 1 removed, 1 amended vs run 101.
    store = LocalRawStore(tmp_path)
    run(store)
    stryker, catheter, _ = fixture_records()
    new = {"k_number": "K251234", "applicant": "New Co", "product_code": "MRN", "decision_date": "2025-10-01"}
    records = [{**stryker, "decision_date": "2021-04-16"}, catheter, new]

    manifest = run(store, run_suffix="102", opener=next_week_opener(records), clock=next_week())

    diff = manifest.sources[0].diff
    assert (diff.previous_run_id, diff.previous_export_date) == ("2026-11-16_101", "2026-11-09")
    assert (diff.added, diff.removed, diff.amended) == (1, 1, 1)
    assert diff.changed_fields == {"decision_date": 1}
    written = RunManifest.from_dict(json.loads((tmp_path / "runs/2026-11-23_102/manifest.json").read_text()))
    assert written.sources[0].diff == diff


def test_run_records_a_zero_diff_without_downloading_when_the_export_is_unchanged(tmp_path):
    # Sample: runs 101 and 102 see the same 2026-11-09 export. Both manifests list the same
    # SHA-256, so the diff is all zeros and neither copy is fetched to compare records.
    store = CountingStore(tmp_path)
    run(store)
    store.fetched.clear()

    manifest = run(store, run_suffix="102", clock=next_week())

    diff = manifest.sources[0].diff
    assert (diff.previous_run_id, diff.added, diff.removed, diff.amended) == ("2026-11-16_101", 0, 0, 0)
    # ingest_export fetches KEY once to confirm the re-published bytes match; nothing more.
    assert [key for key in store.fetched if key.endswith(".zip")] == [KEY]


def test_run_records_no_diff_for_a_source_the_previous_run_did_not_ingest(tmp_path):
    # Sample: run 101 ingests 510(k) only; run 102 adds recall. Recall has no previous
    # snapshot to compare with, so its diff is None while 510(k) gets a zero diff.
    store = LocalRawStore(tmp_path)
    run(store)
    manifest = run_ingestion(
        ["510k", "recall"],
        store,
        run_suffix="102",
        commit_sha=COMMIT,
        writer="github-actions",
        opener=opener_for(fixture_export_zip()),
        clock=next_week(),
    )
    diffs = {s.source: s.diff for s in manifest.sources}
    assert diffs["recall"] is None
    assert diffs["510k"].amended == 0


def test_run_diffs_enforcement_by_recall_number_and_reports_the_reclassified_field(tmp_path):
    # Sample: run 101 stores the 2026-11-16 enforcement export (Z-0001-2026 Class II,
    # Z-0002-2026 Not Yet Classified). Run 102 sees a 2026-11-23 export where FDA has since
    # classified Z-0002-2026 as Class I. Keyed on recall_number, that is 1 amended record
    # whose only changed field is classification.
    store = LocalRawStore(tmp_path)
    run_ingestion(
        ["enforcement"], store, run_suffix="101", commit_sha=COMMIT, writer="github-actions",
        opener=opener_for(fixture_export_zip()),
        clock=fixed_clock("2026-11-16T06:00:04", "2026-11-16T06:01:10"),
    )
    index = load_index()
    index["results"]["device"]["enforcement"]["export_date"] = "2026-11-23"
    reclassified = [dict(r) for r in ENFORCEMENT_RECORDS]
    reclassified[1]["classification"] = "Class I"

    manifest = run_ingestion(
        ["enforcement"], store, run_suffix="102", commit_sha=COMMIT, writer="github-actions",
        opener=opener_for(fixture_export_zip(), index, enforcement_export_zip(reclassified)),
        clock=next_week(),
    )

    [snapshot] = manifest.sources
    assert (snapshot.source, snapshot.export_date, snapshot.objects[0].records) == ("enforcement", "2026-11-23", 2)
    diff = snapshot.diff
    assert (diff.added, diff.removed, diff.amended, diff.changed_fields) == (0, 0, 1, {"classification": 1})


def test_second_run_diffs_an_enforcement_export_with_a_blank_recall_number_instead_of_failing(tmp_path):
    # Sample: like the real 2026-11-16 export, enforcement holds a record with recall_number ""
    # (event 94520, status Ongoing). Run 102's 2026-11-23 export has it Terminated, so the bytes
    # differ and the diff must run: keyed by its event_id fallback, it is 1 amended record.
    keyless = {"recall_number": "", "event_id": "94520", "classification": "Not Yet Classified", "status": "Ongoing"}
    weeks = [[*ENFORCEMENT_RECORDS, keyless], [*ENFORCEMENT_RECORDS, {**keyless, "status": "Terminated"}]]
    first, second = load_index(), load_index()
    first["results"]["device"]["enforcement"]["partitions"][0]["records"] = 3
    first["results"]["device"]["enforcement"]["total_records"] = 3
    second["results"]["device"]["enforcement"]["partitions"][0]["records"] = 3
    second["results"]["device"]["enforcement"]["total_records"] = 3
    second["results"]["device"]["enforcement"]["export_date"] = "2026-11-23"
    store = LocalRawStore(tmp_path)
    for suffix, index, records, clock in (
        ("101", first, weeks[0], fixed_clock("2026-11-16T06:00:04", "2026-11-16T06:01:10")),
        ("102", second, weeks[1], next_week()),
    ):
        manifest = run_ingestion(
            ["enforcement"], store, run_suffix=suffix, commit_sha=COMMIT, writer="github-actions",
            opener=opener_for(fixture_export_zip(), index, enforcement_export_zip(records)), clock=clock,
        )
    diff = manifest.sources[0].diff
    assert (diff.previous_run_id, diff.added, diff.removed, diff.amended) == ("2026-11-16_101", 0, 0, 1)
    assert diff.changed_fields == {"status": 1}


def test_ingest_openfda_cli_ingests_510k_enforcement_and_recall_by_default(tmp_path, monkeypatch):
    # Sample: `filiation-pipeline ingest-openfda` with no --source, as the weekly workflow
    # runs it, against the fixture index (510k, recall, and enforcement entries).
    import filiation.pipeline.cli as cli_module

    store = LocalRawStore(tmp_path)
    monkeypatch.setattr(cli_module, "raw_store_from_env", lambda: store)
    monkeypatch.setattr(cli_module, "default_opener", opener_for(fixture_export_zip()))

    result = CliRunner().invoke(
        cli_module.cli, ["ingest-openfda", "--run-id", "101", "--commit-sha", COMMIT, "--writer", "local"]
    )

    assert result.exit_code == 0, result.output
    assert sorted(s["source"] for s in json.loads(result.output)["sources"]) == ["510k", "enforcement", "recall"]


def test_run_fails_without_a_manifest_when_a_previous_object_no_longer_matches_its_hash(tmp_path):
    # Sample: after run 101, the stored 2026-11-09 zip is replaced on disk by different
    # bytes (bit rot or a bad copy). Run 102 must refuse to diff against it, write no
    # manifest, and leave latest.json on run 101.
    store = LocalRawStore(tmp_path)
    run(store)
    (tmp_path / KEY).write_bytes(make_export_zip({"device-510k-0001-of-0001.json": '{"results": []}'}))
    stryker, catheter, _ = fixture_records()

    with pytest.raises(ExportIntegrityError):
        run(store, run_suffix="102", opener=next_week_opener([stryker, catheter]), clock=next_week())

    assert not store.exists("runs/2026-11-23_102/manifest.json")
    assert json.loads((tmp_path / LATEST_KEY).read_text())["manifest_key"] == MANIFEST_KEY


def test_run_fails_without_a_manifest_when_the_new_export_repeats_a_key(tmp_path):
    # Sample: next week's export lists K210758 twice. The diff can't say which copy is
    # current, so the run stops before writing a manifest; latest.json stays on run 101.
    store = LocalRawStore(tmp_path)
    run(store)
    stryker, catheter, _ = fixture_records()

    with pytest.raises(ExportIntegrityError):
        run(store, run_suffix="102", opener=next_week_opener([stryker, catheter, stryker]), clock=next_week())

    assert not store.exists("runs/2026-11-23_102/manifest.json")
    assert json.loads((tmp_path / LATEST_KEY).read_text())["manifest_key"] == MANIFEST_KEY


def test_run_fails_before_downloading_when_latest_json_names_a_missing_manifest(tmp_path):
    # Sample: latest.json points at runs/2026-11-09_99/manifest.json, which was never
    # written. Diffing against "nothing" would report every record as added, so the run
    # must stop and fetch nothing from openFDA.
    store = LocalRawStore(tmp_path)
    store.write_latest(b'{"manifest_key": "runs/2026-11-09_99/manifest.json", "run_id": "2026-11-09_99", '
                       b'"updated_at": "2026-11-09T06:00:00Z"}')
    opener = opener_for(fixture_export_zip())

    with pytest.raises(FileNotFoundError):
        run(store, opener=opener)

    assert opener.calls == []


def amended_week(store):
    """Runs 101 on the fixture, then returns the opener and records of a week with 1 add, 1 removal, 1 amendment.

    Args:
        store: RawStore run 101 writes into.

    Returns:
        FakeOpener: Opener serving the 2026-11-16 export.
    """
    run(store)
    stryker, catheter, _ = fixture_records()
    new = {"k_number": "K251234", "applicant": "New Co", "product_code": "MRN", "decision_date": "2025-10-01"}
    return next_week_opener([{**stryker, "decision_date": "2021-04-16"}, catheter, new])


def expected_changes() -> list:
    """Lists the per-key changes of the amended week: K210758 amended, K251234 added, K882211 removed.

    Returns:
        list[KeyChange]: The changes in key order.
    """
    return [
        rm.KeyChange("K210758", "amended", ("decision_date",)),
        rm.KeyChange("K251234", "added"),
        rm.KeyChange("K882211", "removed"),
    ]
CHANGES_KEY = "runs/2026-11-23_102/diffs/510k.jsonl.gz"


def test_run_writes_the_change_list_under_the_run_and_links_it_with_its_sha256(tmp_path):
    # Sample: run 101 stores the fixture; run 102 sees K882211 removed, K251234 added and
    # K210758's decision_date corrected. Run 102 must store those three keys and link them.
    store = LocalRawStore(tmp_path)
    manifest = run(store, run_suffix="102", opener=amended_week(store), clock=next_week())

    linked = manifest.sources[0].diff.changes
    data = (tmp_path / CHANGES_KEY).read_bytes()
    assert (linked.key, linked.sha256, linked.size_bytes, linked.records) == (
        CHANGES_KEY, hashlib.sha256(data).hexdigest(), len(data), 3
    )
    assert rm.decode_changes(data) == expected_changes()


def test_run_stores_no_change_list_when_the_export_is_unchanged_or_there_is_no_previous_run(tmp_path):
    # Sample: run 101 (first run, nothing to compare) and run 102 on the same export. Neither
    # changed anything, so neither writes a diffs/ object or links one.
    store = LocalRawStore(tmp_path)
    first = run(store)
    second = run(store, run_suffix="102", clock=next_week())
    assert first.sources[0].diff is None and second.sources[0].diff.changes is None
    assert not list(tmp_path.glob("runs/*/diffs/*"))


def test_rerun_after_a_crash_reuses_an_identical_change_list_left_at_its_key(tmp_path):
    # Sample: run 102 crashed after storing its change list but before its manifest; it is
    # rerun with the same id. The stored list has the same bytes, so the rerun links it.
    store = LocalRawStore(tmp_path)
    opener = amended_week(store)
    store.put_once(CHANGES_KEY, rm.encode_changes(expected_changes()))

    manifest = run(store, run_suffix="102", opener=opener, clock=next_week())

    assert manifest.sources[0].diff.changes.records == 3


def test_rerun_refuses_a_different_change_list_already_at_its_key(tmp_path):
    # Sample: a list with only K251234 already sits at run 102's key. Overwriting a stored
    # object is never allowed, so the run fails and writes no manifest.
    store = LocalRawStore(tmp_path)
    opener = amended_week(store)
    store.put_once(CHANGES_KEY, rm.encode_changes([rm.KeyChange("K251234", "added")]))

    with pytest.raises(KeyExistsError):
        run(store, run_suffix="102", opener=opener, clock=next_week())
    assert not (tmp_path / "runs/2026-11-23_102/manifest.json").exists()
