"""Profiles a source's export for field completeness and 510(k) date anomalies in the record-counting pass (#92, ADR-016)."""

from __future__ import annotations

import bisect
import re
from datetime import date
from typing import Any

from recension.pipeline.crawler.findings import CheckSpec, Finding, register

# Medical Device Amendments: no 510(k) clearance can predate it. A receipt can: K760001-K760003
# were received 1976-05-26, two days before the law took effect.
MDA_DATE = date(1976, 5, 28)
# A source keyed by ids would otherwise grow one path per record and bloat the manifest.
MAX_FIELDS = 1000
MAX_EXAMPLES = 5
DATE_FIELDS = {"510k": ("date_received", "decision_date")}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

FIELD_ALWAYS_EMPTY = register(CheckSpec("completeness.field_always_empty", "report", ("R14",)))
FIELD_CAP = register(CheckSpec("completeness.field_cap", "report", ("R14",)))
NON_OBJECT_RECORD = register(CheckSpec("completeness.non_object_record", "report", ("R14",)))
DATE_UNPARSEABLE = register(CheckSpec("dates.unparseable", "report", ("R14",)))
DECISION_BEFORE_MDA = register(CheckSpec("dates.decision_before_mda", "report", ("R14",)))
DATE_AFTER_EXPORT = register(CheckSpec("dates.after_export", "report", ("R14",)))
DECISION_BEFORE_RECEIVED = register(CheckSpec("dates.decision_before_received", "report", ("R14",)))
COMPLETENESS_CHECKS = (FIELD_ALWAYS_EMPTY, FIELD_CAP, NON_OBJECT_RECORD)
DATE_CHECKS = (DATE_UNPARSEABLE, DECISION_BEFORE_MDA, DATE_AFTER_EXPORT, DECISION_BEFORE_RECEIVED)


def is_empty(value: Any) -> bool:
    """Tells whether a JSON value carries no data.

    Args:
        value: Decoded JSON value.

    Returns:
        bool: True for None, blank or whitespace-only strings, and empty lists or objects.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict)) and not value


def _pointer_segment(key: Any) -> str:
    """Escapes an object key as a JSON-pointer segment (RFC 6901).

    Args:
        key: Object key.

    Returns:
        str: The key with "~" as "~0" and "/" as "~1".
    """
    return str(key).replace("~", "~0").replace("/", "~1")


def _plural(count: int) -> str:
    """Formats a record count with its noun.

    Args:
        count: Number of records.

    Returns:
        str: e.g. "1 record" or "1,000 records".
    """
    return f"{count:,} record" + ("" if count == 1 else "s")


class _Anomaly:
    """A running count of one date rule's hits and its smallest example identifiers."""

    def __init__(self) -> None:
        """Starts with no hits."""
        self.count = 0
        self.examples: list[str] = []
        self.by_field: dict[str, int] = {}

    def add(self, identifier: str, field: str | None = None) -> None:
        """Counts one hit, keeping the smallest identifiers so examples ignore record order.

        Args:
            identifier: The record's k_number, or "#<index>" if it has none.
            field: The date field involved, for rules that report per field.
        """
        self.count += 1
        if field:
            self.by_field[field] = self.by_field.get(field, 0) + 1
        if identifier not in self.examples:
            bisect.insort(self.examples, identifier)
            del self.examples[MAX_EXAMPLES:]


class ExportProfile:
    """Accumulates one source's field completeness and date anomalies across all its partitions."""

    def __init__(self, source: str, export_date: str) -> None:
        """Starts an empty profile.

        Args:
            source: openFDA source name, e.g. "510k"; selects the date rules.
            export_date: The export's YYYY-MM-DD date; no record may postdate it.
        """
        self.source = source
        self.export_date = date.fromisoformat(export_date)
        self._present: dict[str, int] = {}
        self._dropped: set[str] = set()
        self._records = 0
        self._non_objects = 0
        self._anomalies = {check.check_id: _Anomaly() for check in DATE_CHECKS}

    @property
    def checks(self) -> list[str]:
        """Lists the ids of every check this profile applies to its source.

        Returns:
            list[str]: Completeness check ids, plus the date rule ids for sources that have them.
        """
        specs = COMPLETENESS_CHECKS + (DATE_CHECKS if self.source in DATE_FIELDS else ())
        return sorted(spec.check_id for spec in specs)

    @property
    def fields_present(self) -> dict[str, int]:
        """Returns the non-empty record count per JSON-pointer field path, 0 for always-empty paths.

        Returns:
            dict[str, int]: Counts in path order.
        """
        return dict(sorted(self._present.items()))

    def observe(self, record: Any) -> None:
        """Adds one record's fields and date checks to the profile.

        Args:
            record: One element of the export's "results" array.
        """
        index = self._records
        self._records += 1
        if not isinstance(record, dict):
            self._non_objects += 1
            return
        self._walk(record, "")
        if self.source in DATE_FIELDS:
            self._check_dates(record, index)

    def _walk(self, obj: dict[Any, Any], prefix: str) -> None:
        """Counts each field of an object, recursing into nested objects.

        Args:
            obj: The object to walk.
            prefix: JSON pointer of obj, "" at the record root.
        """
        for key, value in obj.items():
            path = f"{prefix}/{_pointer_segment(key)}"
            if path not in self._present:
                if len(self._present) >= MAX_FIELDS:
                    if len(self._dropped) < MAX_FIELDS:
                        self._dropped.add(path)
                    continue
                self._present[path] = 0
            if not is_empty(value):
                self._present[path] += 1
            if isinstance(value, dict):
                self._walk(value, path)

    def _check_dates(self, record: dict[str, Any], index: int) -> None:
        """Applies the source's date rules to one record.

        Args:
            record: The record.
            index: Its position in the export, used when it has no k_number.
        """
        number = record.get("k_number")
        identifier = number.strip() if isinstance(number, str) and number.strip() else f"#{index}"
        parsed: dict[str, date] = {}
        for name in DATE_FIELDS[self.source]:
            value = record.get(name)
            if is_empty(value):
                continue
            try:
                if not isinstance(value, str) or not _ISO_DATE.fullmatch(value.strip()):
                    raise ValueError(value)
                parsed[name] = date.fromisoformat(value.strip())
            except ValueError:
                self._anomalies[DATE_UNPARSEABLE.check_id].add(identifier, name)
        if len(parsed) < sum(not is_empty(record.get(n)) for n in DATE_FIELDS[self.source]):
            return
        received, decided = parsed.get("date_received"), parsed.get("decision_date")
        if decided and decided < MDA_DATE:
            self._anomalies[DECISION_BEFORE_MDA.check_id].add(identifier)
        if any(day > self.export_date for day in parsed.values()):
            self._anomalies[DATE_AFTER_EXPORT.check_id].add(identifier)
        if received and decided and decided < received:
            self._anomalies[DECISION_BEFORE_RECEIVED.check_id].add(identifier)

    def findings(self) -> list[Finding]:
        """Reports everything the profile found, one finding per check, in check-id order.

        Returns:
            list[Finding]: Report-severity findings; empty when every check came back clean.
        """
        found = []
        if empty := [path for path, n in sorted(self._present.items()) if n == 0]:
            shown = ", ".join(empty[:MAX_EXAMPLES]) + (", …" if len(empty) > MAX_EXAMPLES else "")
            found.append(FIELD_ALWAYS_EMPTY.finding(self.source, f"{len(empty):,} fields present but always empty: {shown}"))
        if self._dropped:
            found.append(
                FIELD_CAP.finding(
                    self.source, f"{len(self._dropped):,} field paths beyond the {MAX_FIELDS}-path cap were not counted"
                )
            )
        if self._non_objects:
            found.append(NON_OBJECT_RECORD.finding(self.source, f"{_plural(self._non_objects)} not JSON objects"))
        descriptions = {
            DATE_UNPARSEABLE: "with a date that is not a valid YYYY-MM-DD",
            DECISION_BEFORE_MDA: f"decided before {MDA_DATE.isoformat()}",
            DATE_AFTER_EXPORT: f"dated after the export date {self.export_date.isoformat()}",
            DECISION_BEFORE_RECEIVED: "decided before they were received",
        }
        for spec, text in descriptions.items():
            anomaly = self._anomalies[spec.check_id]
            if not anomaly.count:
                continue
            fields = f" ({', '.join(f'{k} {v:,}' for k, v in sorted(anomaly.by_field.items()))})" if anomaly.by_field else ""
            more = ", …" if anomaly.count > len(anomaly.examples) else ""
            found.append(spec.finding(self.source, f"{_plural(anomaly.count)} {text}{fields}: {', '.join(anomaly.examples)}{more}"))
        return sorted(found, key=lambda f: f.check.check_id)
