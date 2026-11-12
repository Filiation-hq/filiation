"""Unit tests for one ingestion run in stemma.pipeline.crawler.ingestion_run (#42).

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
"""

import hashlib
import json
from datetime import datetime, timezone

import pytest
from click.testing import CliRunner
from openfda_fakes import KEY, fixture_export_zip, load_index, opener_for
from stemma.core.schemas.run_manifest import RunManifest
from stemma.core.storage.raw_store import LATEST_KEY, KeyExistsError, LocalRawStore
from stemma.pipeline.crawler.ingestion_run import run_ingestion
from stemma.pipeline.crawler.openfda_bulk import ExportIntegrityError

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
    # Sample: `stemma-pipeline ingest-openfda --source 510k --run-id 101 --commit-sha <sha>
    # --writer local` with the store and opener patched to fixtures.
    import stemma.pipeline.cli as cli_module

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
