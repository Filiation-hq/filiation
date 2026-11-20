"""Reports how often openFDA amends and removes records between stored snapshots, split by kind of change (#91, ADR-021)."""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filiation.core.schemas.run_manifest import (
    COSMETIC_SINCE,
    KeyChange,
    RunManifest,
    decode_changes,
)
from filiation.core.storage.raw_store import RawStore
from filiation.pipeline.crawler.ingestion_run import fetch_verified

# openFDA's own enrichment block; a change there is openFDA re-deriving it, not FDA amending the record.
ENRICHMENT_FIELD = "openfda"
# Fields that answer #91's questions (class, dates, identifiers such as K-numbers, status), per source.
# The recall export carries no class; a 510(k) has a decision instead of a status.
FIELD_GROUPS: dict[str, dict[str, set[str]]] = {
    "510k": {
        "dates": {"date_received", "decision_date"},
        "decision": {"decision_code", "decision_description", "clearance_type"},
        "identifiers": {"product_code"},
    },
    "recall": {
        "dates": {"event_date_initiated", "event_date_posted", "event_date_terminated", "event_date_created"},
        "identifiers": {"k_numbers", "pma_numbers", "product_code", "res_event_number", "cfres_id"},
        "status": {"recall_status"},
    },
    "enforcement": {
        "class": {"classification"},
        "dates": {"recall_initiation_date", "center_classification_date", "report_date", "termination_date"},
        "identifiers": {"event_id"},
        "status": {"status"},
    },
}


@dataclass(frozen=True)
class SourceAmendments:
    """One run's diff of one source, with its amended keys split by kind of change."""

    run_id: str
    source: str
    previous_run_id: str
    # "stored" when openFDA published a new export that week; "unchanged" when the run reused the last one.
    export_status: str
    # True when the change list marks cosmetic fields (manifest v4+); False for v1-v3 diffs.
    classified: bool
    added: int
    removed: int
    amended: int
    # Keys with a substantive change outside openfda; None when the diff has no change list.
    fda_edits: int | None
    # Keys whose only substantive change is in openfda; None without a change list.
    openfda_only: int | None
    # Keys whose every change is cosmetic; None unless classified.
    cosmetic_only: int | None
    # FDA edits per field, and keys with an FDA edit in each FIELD_GROUPS group.
    fda_fields: dict[str, int] = field(default_factory=dict)
    groups: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Removal:
    """A record key that disappeared from a source, with the run that first missed it."""

    source: str
    key: str
    first_missed_run: str
    # The run in which the key came back, or None while it is still missing.
    returned_run: str | None = None


@dataclass(frozen=True)
class AmendmentReport:
    """Every stored run's per-source amendments and removals, from each source's first snapshot on."""

    # Source to the run of its first snapshot: history before it is reconstructed, not observed.
    since: dict[str, str]
    rows: list[SourceAmendments]
    removals: list[Removal]

    def to_dict(self) -> dict[str, Any]:
        """Converts the report to JSON-ready data.

        Returns:
            dict[str, Any]: since, rows, and removals.
        """
        return {
            "since": dict(self.since),
            "rows": [asdict(row) for row in self.rows],
            "removals": [asdict(removal) for removal in self.removals],
        }


def _load_manifests(store: RawStore, work_dir: Path | None) -> list[RunManifest]:
    """Reads every run manifest in the store, in the order the runs started.

    Args:
        store: Raw store holding runs/<run_id>/manifest.json objects.
        work_dir: Directory for temporary downloads.

    Returns:
        list[RunManifest]: The manifests, oldest start first.

    Raises:
        ValueError: If a manifest is not valid JSON or fails its contract.
    """
    manifests = []
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        for i, key in enumerate(k for k in store.list("runs/") if k.endswith("/manifest.json")):
            text = store.get(key, Path(tmp) / f"{i:05d}.json").read_text(encoding="utf-8")
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{key} is not valid JSON: {exc}") from exc
            manifests.append(RunManifest.from_dict(data))
    return sorted(manifests, key=lambda m: (m.started_at, m.run_id))


def _split(source: str, changes: list[KeyChange], classified: bool) -> dict[str, Any]:
    """Splits one diff's amended keys into FDA edits, openfda-only changes, and cosmetic-only changes.

    Args:
        source: Source name, which picks its FIELD_GROUPS.
        changes: The diff's change list.
        classified: Whether the list marks cosmetic fields; if not, every change counts as substantive.

    Returns:
        dict[str, Any]: fda_edits, openfda_only, cosmetic_only (None unless classified),
            fda_fields, and groups.
    """
    fda_edits = openfda_only = cosmetic_only = 0
    fda_fields: Counter[str] = Counter()
    groups: Counter[str] = Counter()
    for change in changes:
        if change.change != "amended":
            continue
        substantive = set(change.substantive_fields)
        fda = substantive - {ENRICHMENT_FIELD}
        if fda:
            fda_edits += 1
            fda_fields.update(fda)
            groups.update(name for name, members in FIELD_GROUPS.get(source, {}).items() if fda & members)
        elif substantive:
            openfda_only += 1
        else:
            cosmetic_only += 1
    return {
        "fda_edits": fda_edits,
        "openfda_only": openfda_only,
        "cosmetic_only": cosmetic_only if classified else None,
        "fda_fields": dict(sorted(fda_fields.items())),
        "groups": dict(sorted(groups.items())),
    }


def _track_removals(open_removals: dict[tuple[str, str], int], removals: list[Removal], source: str, run_id: str, changes: list[KeyChange]) -> None:
    """Records keys a run first missed, and closes removals whose key the run added back.

    Args:
        open_removals: (source, key) to the index in removals of its still-open removal; updated.
        removals: Every removal so far; appended to and updated.
        source: Source name.
        run_id: The run whose change list this is.
        changes: The run's change list for the source.
    """
    for change in changes:
        slot = (source, change.key)
        if change.change == "removed" and slot not in open_removals:
            open_removals[slot] = len(removals)
            removals.append(Removal(source, change.key, run_id))
        elif change.change == "added" and slot in open_removals:
            index = open_removals.pop(slot)
            removals[index] = Removal(source, change.key, removals[index].first_missed_run, run_id)


def amendment_report(store: RawStore, work_dir: Path | None = None) -> AmendmentReport:
    """Builds the amendment and removal report over every run in the store.

    Args:
        store: Raw store holding the run manifests and their change lists; it is only read.
        work_dir: Directory for temporary downloads.

    Returns:
        AmendmentReport: Each source's first snapshot, one row per later diff, and every removal.

    Raises:
        ValueError: If a manifest or a change list is malformed.
        ExportIntegrityError: If a change list's bytes no longer match the hash its manifest recorded.
    """
    since: dict[str, str] = {}
    rows: list[SourceAmendments] = []
    removals: list[Removal] = []
    open_removals: dict[tuple[str, str], int] = {}
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        for manifest in _load_manifests(store, Path(tmp)):
            for snapshot in manifest.sources:
                since.setdefault(snapshot.source, manifest.run_id)
                diff = snapshot.diff
                if diff is None:
                    continue
                classified = manifest.schema_version >= COSMETIC_SINCE
                counts = {"added": diff.added, "removed": diff.removed, "amended": diff.amended}
                if diff.changes is not None:
                    (path,) = fetch_verified(store, [diff.changes], Path(tmp) / manifest.run_id / snapshot.source)
                    changes = decode_changes(path.read_bytes())
                    split = _split(snapshot.source, changes, classified)
                    _track_removals(open_removals, removals, snapshot.source, manifest.run_id, changes)
                elif diff.total == 0:
                    split = _split(snapshot.source, [], classified)
                else:
                    split = {"fda_edits": None, "openfda_only": None, "cosmetic_only": None}
                rows.append(
                    SourceAmendments(manifest.run_id, snapshot.source, diff.previous_run_id, snapshot.status, classified, **counts, **split)
                )
    return AmendmentReport(since, rows, removals)


def _cell(value: int | None) -> str:
    """Formats a count for the Markdown table, with a dash where the run couldn't measure it.

    Args:
        value: A count, or None.

    Returns:
        str: The count, or "–".
    """
    return "–" if value is None else f"{value:,}"


def report_markdown(report: AmendmentReport, max_removals: int = 20) -> str:
    """Renders the report as Markdown for the weekly run's job summary.

    Args:
        report: Output of amendment_report.
        max_removals: How many removals to list before summarizing the rest.

    Returns:
        str: A heading, the point-in-time note, a table of runs, and the removals.
    """
    lines = ["## openFDA amendments and removals", ""]
    if not report.since:
        return "\n".join([*lines, "No runs yet; run `filiation-pipeline ingest-openfda` first.", ""])
    starts = ", ".join(f"{source} point-in-time since {run}" for source, run in sorted(report.since.items()))
    lines += [
        f"Observed from stored snapshots only: {starts}. Anything earlier is reconstructed from a later pull, not observed.",
        "FDA edits are substantive changes outside `openfda`; cosmetic changes are whitespace or list order only (ADR-021). "
        "A dash means the run's manifest predates the measure.",
        "",
        "| Run | Source | Export | Added | Removed | Amended | FDA edits | openfda only | Cosmetic only | FDA edits by group |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report.rows:
        groups = ", ".join(f"{name} {count:,}" for name, count in row.groups.items()) or "–"
        lines.append(
            f"| {row.run_id} | {row.source} | {'new' if row.export_status == 'stored' else 'same as last'} | {row.added:,} | {row.removed:,} | {row.amended:,} | "
            f"{_cell(row.fda_edits)} | {_cell(row.openfda_only)} | {_cell(row.cosmetic_only)} | {groups} |"
        )
    if report.removals:
        lines += ["", f"### Removed keys ({len(report.removals):,})", ""]
        for removal in report.removals[:max_removals]:
            back = f", back in {removal.returned_run}" if removal.returned_run else ", still missing"
            lines.append(f"- {removal.source} `{removal.key}`: first missed in {removal.first_missed_run}{back}")
        if len(report.removals) > max_removals:
            lines.append(f"- … and {len(report.removals) - max_removals:,} more (`--format json` lists all)")
    return "\n".join([*lines, ""])
