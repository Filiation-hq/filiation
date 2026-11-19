"""Data contract for ingestion run manifests and the latest.json pointer (ADR-010, #42; v2: ADR-016, #92)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

SCHEMA_VERSION = 2
# v1 manifests predate completeness and checks; they still load, with those fields None.
SUPPORTED_SCHEMA_VERSIONS = (1, 2)
V2_SOURCE_FIELDS = ("fields_present", "checks", "findings")
SEVERITIES = ("fail", "report")
CHECK_ID = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
REGISTER_ID = re.compile(r"R\d{2}")
WRITERS = ("github-actions", "oracle-vm", "local")
STATUSES = ("stored", "unchanged")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_RUN_ID = re.compile(r"(\d{4}-\d{2}-\d{2})_[A-Za-z0-9-]+")
_MANIFEST_KEY = re.compile(r"runs/\d{4}-\d{2}-\d{2}_[A-Za-z0-9-]+/manifest\.json")


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
class SourceDiff:
    """How one source's export differs from the previous run's: records added, removed, and amended."""

    previous_run_id: str
    previous_export_date: str | None
    added: int
    removed: int
    amended: int
    changed_fields: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validates the counts and stores changed_fields in field-name order.

        Raises:
            ValueError: If a count is negative, amended and changed_fields disagree about
                whether anything was amended, a field count is outside 1..amended, or
                previous_export_date is not YYYY-MM-DD.
        """
        if min(self.added, self.removed, self.amended) < 0:
            raise ValueError(f"diff counts must be >= 0, got {self.added}/{self.removed}/{self.amended}")
        if bool(self.amended) != bool(self.changed_fields):
            raise ValueError(f"{self.amended} amended records but changed_fields {self.changed_fields!r}")
        if bad := {k: n for k, n in self.changed_fields.items() if not 1 <= n <= self.amended}:
            raise ValueError(f"changed_fields counts {bad} must be between 1 and amended={self.amended}")
        if self.previous_export_date is not None:
            _check_iso_date(self.previous_export_date, "previous_export_date")
        object.__setattr__(self, "changed_fields", dict(sorted(self.changed_fields.items())))

    def to_dict(self) -> dict[str, Any]:
        """Converts the diff to plain JSON-ready data.

        Returns:
            dict[str, Any]: The diff's fields.
        """
        return asdict(self)


def check_finding_fields(check: str, severity: str, register_ids: tuple[str, ...]) -> None:
    """Validates a check id, severity, and bias-register ids, wherever a finding is declared or stored.

    Args:
        check: Dotted lowercase check id, e.g. "export_counts.total".
        severity: "fail" or "report".
        register_ids: Distinct bias-register factor ids such as "R14"; may be empty.

    Raises:
        ValueError: If any of the three is malformed.
    """
    if not isinstance(check, str) or not CHECK_ID.fullmatch(check):
        raise ValueError(f"check id {check!r} must be dotted lowercase, e.g. 'export_counts.total'")
    if severity not in SEVERITIES:
        raise ValueError(f"severity {severity!r} must be one of {SEVERITIES}")
    bad = [r for r in register_ids if not isinstance(r, str) or not REGISTER_ID.fullmatch(r)]
    if bad or len(set(register_ids)) != len(register_ids):
        raise ValueError(f"register ids {register_ids!r} must be distinct ids like 'R18'")


@dataclass(frozen=True)
class ManifestFinding:
    """One check's result recorded in a run manifest, tied to the bias-register factors it controls."""

    check: str
    severity: str
    register_ids: tuple[str, ...]
    subject: str
    message: str

    def __post_init__(self) -> None:
        """Validates the fields and stores register_ids as a tuple.

        Raises:
            ValueError: If the check id, severity, or a register id is malformed.
        """
        object.__setattr__(self, "register_ids", tuple(self.register_ids))
        check_finding_fields(self.check, self.severity, self.register_ids)

    def to_dict(self) -> dict[str, Any]:
        """Converts the finding to JSON-ready data with the same keys a pipeline Finding writes.

        Returns:
            dict[str, Any]: check, severity, register_ids (a list), subject, and message.
        """
        return {**asdict(self), "register_ids": list(self.register_ids)}


@dataclass(frozen=True)
class SourceSnapshot:
    """Everything one source contributed to a run, as of its openFDA export_date."""

    source: str
    status: str
    objects: list[ManifestObject] = field(default_factory=list)
    export_date: str | None = None
    last_updated: str | None = None
    diff: SourceDiff | None = None
    targets_from: str | None = None
    fields_present: dict[str, int] | None = None
    checks: list[str] | None = None
    findings: list[ManifestFinding] | None = None

    def __post_init__(self) -> None:
        """Validates the status, dates, targets_from, and the completeness, checks, and findings.

        None for fields_present, checks, or findings means "not checked"; empty means checked.

        Raises:
            ValueError: If status is unknown, either date is not YYYY-MM-DD, targets_from is not
                a runs/<run_id>/manifest.json key, or the completeness fields are inconsistent
                (see _check_quality).
        """
        self._check_quality()
        if self.targets_from is not None and not _MANIFEST_KEY.fullmatch(self.targets_from):
            raise ValueError(f"targets_from {self.targets_from!r} is not a runs/<run_id>/manifest.json key")
        if self.status not in STATUSES:
            raise ValueError(f"status {self.status!r} not in {STATUSES}")
        if self.export_date is not None:
            _check_iso_date(self.export_date, "export_date")
        if self.last_updated is not None:
            _check_iso_date(self.last_updated, "last_updated")

    def _check_quality(self) -> None:
        """Validates fields_present, checks, and findings, and stores them in a stable order.

        Raises:
            ValueError: If a field path is not a JSON pointer or its count is not an int from 0
                to the source's record total; a check id repeats; findings exist without
                checks or cite a check that did not run; or a finding has severity "fail",
                which stops a run before any manifest is written.
        """
        if self.fields_present is not None:
            counts = [o.records for o in self.objects]
            limit = sum(counts) if None not in counts else None
            bad = {
                path: n
                for path, n in self.fields_present.items()
                if not isinstance(path, str)
                or not path.startswith("/")
                or type(n) is not int
                or n < 0
                or (limit is not None and n > limit)
            }
            if bad:
                raise ValueError(f"fields_present {bad} must map /paths to counts from 0 to {limit}")
            object.__setattr__(self, "fields_present", dict(sorted(self.fields_present.items())))
        if self.checks is not None:
            if len(set(self.checks)) != len(self.checks):
                raise ValueError(f"checks {self.checks!r} lists a check id more than once")
            object.__setattr__(self, "checks", sorted(self.checks))
        if self.findings is None:
            return
        if self.checks is None:
            raise ValueError("findings were recorded without the checks that produced them")
        object.__setattr__(
            self, "findings", [f if isinstance(f, ManifestFinding) else ManifestFinding(**f) for f in self.findings]
        )
        for finding in self.findings:
            if finding.check not in self.checks:
                raise ValueError(f"finding from {finding.check!r}, which is not among the checks that ran")
            if finding.severity == "fail":
                raise ValueError(f"fail finding {finding.check!r} in a written manifest: a fail stops the run")


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
                key-safe, commit_sha isn't a full 40-character SHA, writer is unknown,
                schema_version is unsupported, or a v1 manifest carries v2 source fields.
        """
        _check_iso_date(self.run_date, "run_date")
        match = _RUN_ID.fullmatch(self.run_id)
        if not match or match.group(1) != self.run_date:
            raise ValueError(f"run_id {self.run_id!r} must be '{self.run_date}_<id>' using [A-Za-z0-9-]")
        if not _COMMIT.fullmatch(self.commit_sha):
            raise ValueError(f"commit_sha {self.commit_sha!r} is not a full 40-character SHA")
        if self.writer not in WRITERS:
            raise ValueError(f"writer {self.writer!r} not in {WRITERS}")
        if type(self.schema_version) is not int or self.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported manifest schema_version {self.schema_version!r}")
        if self.schema_version == 1 and any(
            getattr(s, name) is not None for s in self.sources for name in V2_SOURCE_FIELDS
        ):
            raise ValueError(f"A schema_version 1 manifest cannot carry {V2_SOURCE_FIELDS}")

    @property
    def manifest_key(self) -> str:
        """Returns the store key this manifest is written to.

        Returns:
            str: runs/<run_id>/manifest.json.
        """
        return f"runs/{self.run_id}/manifest.json"

    def to_dict(self) -> dict[str, Any]:
        """Converts the manifest to plain JSON-ready data, schema_version first.

        A v1 manifest is written back without v2 source fields, so it never claims checks.

        Returns:
            dict[str, Any]: Nested dict of the manifest.
        """
        data = asdict(self)
        for source in data["sources"]:
            if self.schema_version == 1:
                for name in V2_SOURCE_FIELDS:
                    source.pop(name)
            elif source["findings"] is not None:
                source["findings"] = [{**f, "register_ids": list(f["register_ids"])} for f in source["findings"]]
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
        version = data.get("schema_version")
        if type(version) is not int or version not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(f"Unsupported manifest schema_version {version!r}")
        if version == 1 and any(name in s for s in data["sources"] for name in V2_SOURCE_FIELDS):
            raise ValueError(f"A schema_version 1 manifest cannot carry {V2_SOURCE_FIELDS}")
        sources = [
            SourceSnapshot(
                source=s["source"],
                status=s["status"],
                export_date=s.get("export_date"),
                last_updated=s.get("last_updated"),
                objects=[ManifestObject(**o) for o in s["objects"]],
                diff=SourceDiff(**s["diff"]) if s.get("diff") else None,
                targets_from=s.get("targets_from"),
                fields_present=s.get("fields_present"),
                checks=s.get("checks"),
                findings=[ManifestFinding(**f) for f in s["findings"]] if s.get("findings") is not None else None,
            )
            for s in data["sources"]
        ]
        fields = {k: v for k, v in data.items() if k not in ("sources", "schema_version")}
        return cls(sources=sources, schema_version=version, **fields)


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
