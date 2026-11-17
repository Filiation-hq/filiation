"""Runs one batch ingestion and records it in a write-once run manifest (ADR-010, #42)."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from recension.core.schemas.run_manifest import (
    LatestPointer,
    ManifestObject,
    RunManifest,
    SourceDiff,
    SourceSnapshot,
)
from recension.core.storage.raw_store import LATEST_KEY, KeyExistsError, RawStore
from recension.core.utils.hashing import compute_sha256
from recension.pipeline.crawler.export_diff import RECORD_KEYS, RecordDiff, diff_exports
from recension.pipeline.crawler.openfda_bulk import (
    ExportIntegrityError,
    ExportResult,
    Opener,
    default_opener,
    ingest_export,
)


def utc_now() -> datetime:
    """Returns the current time in UTC.

    Returns:
        datetime: Timezone-aware current UTC time.
    """
    return datetime.now(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """Formats a UTC datetime as a second-precision ISO string ending in Z.

    Args:
        moment: Timezone-aware datetime.

    Returns:
        str: e.g. "2026-11-16T06:00:04Z".
    """
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot(result: ExportResult) -> SourceSnapshot:
    """Converts one source's ingest result into its manifest entry.

    Args:
        result: Outcome of ingest_export for one source.

    Returns:
        SourceSnapshot: "stored" if any partition was newly written, else "unchanged", with the
            newest meta.last_updated across partitions.
    """
    objects = [ManifestObject(p.key, p.sha256, p.size_bytes, p.records) for p in result.partitions]
    status = "stored" if any(p.status == "stored" for p in result.partitions) else "unchanged"
    dates = [p.last_updated for p in result.partitions if p.last_updated]
    return SourceSnapshot(
        source=result.source,
        status=status,
        objects=objects,
        export_date=result.export_date,
        last_updated=max(dates) if dates else None,
    )


def load_latest_manifest(store: RawStore, work_dir: Path | None = None) -> RunManifest | None:
    """Reads the manifest that latest.json currently names.

    Args:
        store: Raw store holding latest.json and the runs/ manifests.
        work_dir: Directory for temporary downloads.

    Returns:
        RunManifest | None: The current snapshot's manifest, or None if latest.json doesn't exist.

    Raises:
        FileNotFoundError: If latest.json names a manifest that is not in the store.
        ValueError: If either document is malformed or has an unsupported schema_version.
    """
    if not store.exists(LATEST_KEY):
        return None
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        pointer = LatestPointer.from_dict(json.loads(store.get(LATEST_KEY, Path(tmp) / "latest.json").read_text()))
        if not store.exists(pointer.manifest_key):
            raise FileNotFoundError(f"latest.json names {pointer.manifest_key}, which is not in the store")
        data = json.loads(store.get(pointer.manifest_key, Path(tmp) / "manifest.json").read_text())
    return RunManifest.from_dict(data)


def fetch_verified(store: RawStore, objects: list[ManifestObject], dest_dir: Path) -> list[Path]:
    """Copies a snapshot's objects out of the store, checking each against its manifest hash.

    Args:
        store: Raw store to read from.
        objects: The objects a manifest lists for one source.
        dest_dir: Directory to copy them into.

    Returns:
        list[Path]: Local copies, in the manifest's order.

    Raises:
        ExportIntegrityError: If an object's bytes no longer match the SHA-256 its manifest recorded.
    """
    paths = []
    for i, obj in enumerate(objects):
        path = store.get(obj.key, dest_dir / f"{i:04d}.zip")
        if (actual := compute_sha256(path)) != obj.sha256:
            raise ExportIntegrityError(f"{obj.key} hashes to {actual[:12]}, manifest says {obj.sha256[:12]}")
        paths.append(path)
    return paths


def _diff_source(
    store: RawStore, snapshot: SourceSnapshot, previous: RunManifest | None, work_dir: Path | None
) -> SourceDiff | None:
    """Diffs one source's new snapshot against the same source in the previous manifest.

    Args:
        store: Raw store holding both snapshots' objects.
        snapshot: This run's snapshot of the source.
        previous: The manifest latest.json named when this run started, if any.
        work_dir: Directory for temporary downloads.

    Returns:
        SourceDiff | None: The diff, or None if the previous run did not ingest this source.

    Raises:
        ExportIntegrityError: If a stored object fails its hash check or either export has a
            duplicate or unkeyable record.
    """
    prior = next((s for s in previous.sources if s.source == snapshot.source), None) if previous else None
    if prior is None:
        return None
    if sorted(o.sha256 for o in prior.objects) == sorted(o.sha256 for o in snapshot.objects):
        counts = RecordDiff(added=0, removed=0, amended=0)
    else:
        with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
            old = fetch_verified(store, prior.objects, Path(tmp) / "previous")
            new = fetch_verified(store, snapshot.objects, Path(tmp) / "current")
            counts = diff_exports(RECORD_KEYS[snapshot.source], old, new)
    return SourceDiff(
        previous_run_id=previous.run_id,
        previous_export_date=prior.export_date,
        added=counts.added,
        removed=counts.removed,
        amended=counts.amended,
        changed_fields=counts.changed_fields,
    )


def run_ingestion(
    sources: list[str],
    store: RawStore,
    *,
    run_suffix: str,
    commit_sha: str,
    writer: str,
    opener: Opener = default_opener,
    clock: Callable[[], datetime] = utc_now,
    work_dir: Path | None = None,
) -> RunManifest:
    """Ingests and diffs each source, writes the run manifest once, then moves latest.json to it.

    Each source is diffed against the manifest latest.json names when the run starts.

    Args:
        sources: openFDA sources to ingest, e.g. ["510k", "recall", "enforcement"].
        store: Raw store to write into.
        run_suffix: Unique id within the day, e.g. the GitHub Actions run id.
        commit_sha: Full SHA of the code that ran.
        writer: "github-actions", "oracle-vm", or "local".
        opener: Callable returning a byte stream for a URL.
        clock: Returns the current UTC time; injectable for tests.
        work_dir: Directory for temporary downloads.

    Returns:
        RunManifest: The manifest that was written.

    Raises:
        ExportIntegrityError: If any export fails its record-count check, a stored object
            fails its hash check, or a diff finds a duplicate or unkeyable record; no
            manifest is written.
        FileNotFoundError: If latest.json names a manifest that is missing; nothing is downloaded.
        ExportConflictError: If an export_date was re-published with different bytes.
        KeyExistsError: If a manifest for this run id already exists.
        ValueError: If the run id, commit, or writer is invalid.
    """
    started = clock()
    run_date = started.astimezone(timezone.utc).date().isoformat()
    # Built before downloading so a bad run id, commit, or writer fails fast.
    skeleton = RunManifest(
        run_id=f"{run_date}_{run_suffix}",
        run_date=run_date,
        started_at=iso_utc(started),
        finished_at=iso_utc(started),
        commit_sha=commit_sha,
        writer=writer,
    )
    if store.exists(skeleton.manifest_key):
        raise KeyExistsError(skeleton.manifest_key)
    previous = load_latest_manifest(store, work_dir)
    snapshots = []
    for source in sources:
        snapshot = _snapshot(ingest_export(source, store, opener=opener, work_dir=work_dir))
        snapshots.append(replace(snapshot, diff=_diff_source(store, snapshot, previous, work_dir)))
    manifest = replace(skeleton, sources=snapshots, finished_at=iso_utc(clock()))
    store.put_once(manifest.manifest_key, manifest.to_json())
    store.write_latest(LatestPointer.for_manifest(manifest).to_json())
    return manifest
