"""Ingestion check findings, each tied to the bias-register factors its check controls (#46, #92)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from filiation.core.schemas.run_manifest import check_finding_fields


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
        check_finding_fields(self.check_id, self.severity, self.register_ids)

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
