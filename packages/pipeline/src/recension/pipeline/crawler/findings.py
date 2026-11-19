"""Ingestion check findings, each tied to the bias-register factors its check controls (#46, #92)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

SEVERITIES = ("fail", "report")
_CHECK_ID = re.compile(r"[a-z0-9_]+(\.[a-z0-9_]+)+")
_REGISTER_ID = re.compile(r"R\d{2}")


@dataclass(frozen=True)
class CheckSpec:
    """A declared ingestion check: its stable id, what a finding does to the run, and the factors it controls."""

    check_id: str
    severity: str
    register_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validates the id, severity, and register ids.

        Raises:
            ValueError: If check_id is not dotted lowercase, severity is not "fail" or "report",
                or a register id is not R plus two digits or appears twice.
        """
        if not _CHECK_ID.fullmatch(self.check_id):
            raise ValueError(f"check id {self.check_id!r} must be dotted lowercase, e.g. 'export_counts.total'")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity {self.severity!r} must be one of {SEVERITIES}")
        bad = [r for r in self.register_ids if not isinstance(r, str) or not _REGISTER_ID.fullmatch(r)]
        if bad or len(set(self.register_ids)) != len(self.register_ids):
            raise ValueError(f"register ids {self.register_ids!r} must be distinct ids like 'R18'")

    def finding(self, subject: str, message: str) -> Finding:
        """Builds a finding of this check against one subject.

        Args:
            subject: What was checked, e.g. "510k" or "510k/device-510k-0001-of-0001.json.zip".
            message: What was observed, with the numbers involved.

        Returns:
            Finding: The finding.
        """
        return Finding(self, subject, message)


@dataclass(frozen=True)
class Finding:
    """One check's result against one subject."""

    check: CheckSpec
    subject: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Serializes the finding with fixed keys for logs and, later, run manifests.

        Returns:
            dict[str, Any]: check, severity, register_ids, subject, and message.
        """
        return {
            "check": self.check.check_id,
            "severity": self.check.severity,
            "register_ids": list(self.check.register_ids),
            "subject": self.subject,
            "message": self.message,
        }


CHECKS: dict[str, CheckSpec] = {}


def register(spec: CheckSpec, registry: dict[str, CheckSpec] | None = None) -> CheckSpec:
    """Adds a check to the registry so each bias-register factor's controls can be listed.

    Args:
        spec: The check to declare.
        registry: Registry to add to; defaults to the module-wide CHECKS.

    Returns:
        CheckSpec: The registered spec (the existing one if an identical spec was registered).

    Raises:
        ValueError: If a different spec is already registered under the same check id.
    """
    registry = CHECKS if registry is None else registry
    existing = registry.setdefault(spec.check_id, spec)
    if existing != spec:
        raise ValueError(f"check id {spec.check_id!r} is already registered as {existing!r}")
    return existing


def failures(findings: Iterable[Finding]) -> list[Finding]:
    """Keeps the findings whose check stops the run.

    Args:
        findings: Findings from any checks.

    Returns:
        list[Finding]: The "fail" findings, in order.
    """
    return [f for f in findings if f.check.severity == "fail"]
