"""Pytest plugin that records each test's outcome as JSON for the fail-first check (#73)."""

from __future__ import annotations

import json
import os
from typing import Any

_OUTCOMES: dict[str, list[str]] = {}


def _reason(report: Any) -> str:
    """Extracts the one-line error from a failed report, preferring pytest's "E" lines.

    Args:
        report: A pytest TestReport or CollectReport.

    Returns:
        str: The first "E   ..." line without its prefix, or the last non-blank line.
    """
    lines = [line for line in report.longreprtext.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("E "):
            return line[1:].strip()
    return lines[-1].strip() if lines else "unknown failure"


def _record(nodeid: str, outcome: str) -> None:
    """Appends an outcome under the test's id with any parametrize suffix removed.

    Args:
        nodeid: Pytest node id, e.g. "tests/unit/test_a.py::test_x[case]".
        outcome: "passed", "skipped", "<phase>-failed: <reason>", or "collection-error: <reason>".
    """
    _OUTCOMES.setdefault(nodeid.split("[", 1)[0], []).append(outcome)


def pytest_collectreport(report: Any) -> None:
    """Records modules that fail to collect, such as an import of code that doesn't exist yet.

    Args:
        report: Pytest collection report.
    """
    if report.failed:
        _record(report.nodeid, f"collection-error: {_reason(report)}")


def pytest_runtest_logreport(report: Any) -> None:
    """Records each test phase that failed, and each test call that passed or skipped.

    Args:
        report: Pytest test report for one phase (setup, call, or teardown).
    """
    if report.failed:
        _record(report.nodeid, f"{report.when}-failed: {_reason(report)}")
    elif report.when == "call":
        _record(report.nodeid, "passed" if report.passed else "skipped")
    elif report.when == "setup" and report.skipped:
        _record(report.nodeid, "skipped")


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """Writes all recorded outcomes to the file named by TDD_PROBE_OUT.

    Args:
        session: Pytest session.
        exitstatus: Pytest exit status (unused; outcomes carry the detail).
    """
    path = os.environ.get("TDD_PROBE_OUT")
    if path:
        with open(path, "w", encoding="utf-8") as out:
            json.dump(_OUTCOMES, out, indent=2)
