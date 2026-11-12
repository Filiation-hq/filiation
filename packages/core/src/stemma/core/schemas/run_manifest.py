"""Data contract for ingestion run manifests and the latest.json pointer (ADR-010, #42)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

SCHEMA_VERSION = 1
WRITERS = ("github-actions", "oracle-vm", "local")
STATUSES = ("stored", "unchanged")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_RUN_ID = re.compile(r"(\d{4}-\d{2}-\d{2})_[A-Za-z0-9-]+")


def _check_iso_date(value: str, name: str) -> None:
    """Checks that a value is a YYYY-MM-DD date.

    Args:
        value: The candidate date string.
        name: Field name for the error message.

    Raises:
        ValueError: If value is not a valid ISO calendar date.
    """
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} {value!r} is not YYYY-MM-DD") from exc


@dataclass(frozen=True)
class ManifestObject:
    """One raw object a run relies on: its key, content hash, size, and record count."""

    key: str
    sha256: str
    size_bytes: int
    records: int | None = None

    def __post_init__(self) -> None:
        """Validates the hash, size, and record count.

        Raises:
            ValueError: If sha256 is not 64 lowercase hex characters or a count is negative.
        """
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError(f"sha256 {self.sha256!r} is not 64 lowercase hex characters")
        if self.size_bytes < 0:
            raise ValueError(f"size_bytes must be >= 0, got {self.size_bytes}")
        if self.records is not None and self.records < 0:
            raise ValueError(f"records must be >= 0, got {self.records}")


@dataclass(frozen=True)
class SourceSnapshot:
    """Everything one source contributed to a run, as of its openFDA export_date."""

    source: str
    status: str
    objects: list[ManifestObject] = field(default_factory=list)
    export_date: str | None = None
    last_updated: str | None = None

    def __post_init__(self) -> None:
        """Validates the status, export_date, and openFDA's meta.last_updated.

        Raises:
            ValueError: If status is unknown or either date is not YYYY-MM-DD.
        """
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} not in {STATUSES}")
        if self.export_date is not None:
            _check_iso_date(self.export_date, "export_date")
        if self.last_updated is not None:
            _check_iso_date(self.last_updated, "last_updated")


@dataclass(frozen=True)
class RunManifest:
    """The write-once record of one ingestion run: which raw objects it saw and which code ran."""

    run_id: str
    run_date: str
    started_at: str
    finished_at: str
    commit_sha: str
    writer: str
    sources: list[SourceSnapshot] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Validates the run identity, commit, and writer.

        Raises:
            ValueError: If run_date is not ISO, run_id doesn't start with run_date or isn't
                key-safe, commit_sha isn't a full 40-character SHA, or writer is unknown.
        """
        _check_iso_date(self.run_date, "run_date")
        match = _RUN_ID.fullmatch(self.run_id)
        if not match or match.group(1) != self.run_date:
            raise ValueError(f"run_id {self.run_id!r} must be '{self.run_date}_<id>' using [A-Za-z0-9-]")
        if not _COMMIT.fullmatch(self.commit_sha):
            raise ValueError(f"commit_sha {self.commit_sha!r} is not a full 40-character SHA")
        if self.writer not in WRITERS:
            raise ValueError(f"writer {self.writer!r} not in {WRITERS}")

    @property
    def manifest_key(self) -> str:
        """Returns the store key this manifest is written to.

        Returns:
            str: runs/<run_id>/manifest.json.
        """
        return f"runs/{self.run_id}/manifest.json"

    def to_dict(self) -> dict[str, Any]:
        """Converts the manifest to plain JSON-ready data, schema_version first.

        Returns:
            dict[str, Any]: Nested dict of the manifest.
        """
        data = asdict(self)
        return {"schema_version": data.pop("schema_version"), **data}

    def to_json(self) -> bytes:
        """Serializes the manifest as indented UTF-8 JSON.

        Returns:
            bytes: The manifest document.
        """
        return json.dumps(self.to_dict(), indent=2).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunManifest:
        """Builds a manifest from parsed JSON.

        Args:
            data: Dict produced by to_dict or read from manifest.json.

        Returns:
            RunManifest: The validated manifest.

        Raises:
            ValueError: If the schema_version is unsupported or any field is invalid.
        """
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"Unsupported manifest schema_version {data.get('schema_version')!r}")
        sources = [
            SourceSnapshot(
                source=s["source"],
                status=s["status"],
                export_date=s.get("export_date"),
                last_updated=s.get("last_updated"),
                objects=[ManifestObject(**o) for o in s["objects"]],
            )
            for s in data["sources"]
        ]
        fields = {k: v for k, v in data.items() if k not in ("sources", "schema_version")}
        return cls(sources=sources, **fields)


@dataclass(frozen=True)
class LatestPointer:
    """The contents of latest.json: which manifest describes the current raw snapshot."""

    manifest_key: str
    run_id: str
    updated_at: str

    @classmethod
    def for_manifest(cls, manifest: RunManifest) -> LatestPointer:
        """Builds the pointer for a manifest that has just been written.

        Args:
            manifest: The manifest latest.json should name.

        Returns:
            LatestPointer: Pointer stamped with the manifest's finished_at.
        """
        return cls(manifest.manifest_key, manifest.run_id, manifest.finished_at)

    def to_json(self) -> bytes:
        """Serializes the pointer as UTF-8 JSON.

        Returns:
            bytes: The latest.json document.
        """
        return json.dumps(asdict(self), indent=2).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LatestPointer:
        """Builds a pointer from parsed latest.json.

        Args:
            data: Dict with manifest_key, run_id, and updated_at.

        Returns:
            LatestPointer: The pointer.
        """
        return cls(**data)
