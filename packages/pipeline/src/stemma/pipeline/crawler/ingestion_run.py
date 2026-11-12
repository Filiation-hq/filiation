"""Runs one batch ingestion and records it in a write-once run manifest (ADR-010, #42)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from stemma.core.schemas.run_manifest import (
    LatestPointer,
    ManifestObject,
    RunManifest,
    SourceSnapshot,
)
from stemma.core.storage.raw_store import KeyExistsError, RawStore
from stemma.pipeline.crawler.openfda_bulk import ExportResult, Opener, default_opener, ingest_export


def utc_now() -> datetime:
    """Returns the current time in UTC.

    Returns:
        datetime: Timezone-aware current UTC time.
    """
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
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
        SourceSnapshot: "stored" if any partition was newly written, else "unchanged".
    """
    objects = [ManifestObject(p.key, p.sha256, p.size_bytes, p.records) for p in result.partitions]
    status = "stored" if any(p.status == "stored" for p in result.partitions) else "unchanged"
    return SourceSnapshot(
        source=result.source, status=status, objects=objects, export_date=result.export_date
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
    """Ingests each source, writes the run manifest once, then moves latest.json to it.

    Args:
        sources: openFDA sources to ingest, e.g. ["510k", "recall"].
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
        ExportIntegrityError: If any export fails its record-count check; no manifest is written.
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
        started_at=_iso(started),
        finished_at=_iso(started),
        commit_sha=commit_sha,
        writer=writer,
    )
    if store.exists(skeleton.manifest_key):
        raise KeyExistsError(skeleton.manifest_key)
    snapshots = [_snapshot(ingest_export(s, store, opener=opener, work_dir=work_dir)) for s in sources]
    manifest = replace(skeleton, sources=snapshots, finished_at=_iso(clock()))
    store.put_once(manifest.manifest_key, manifest.to_json())
    store.write_latest(LatestPointer.for_manifest(manifest).to_json())
    return manifest
