"""Data contract and schema for FDA medical device recall events (ADR-012, ADR-013)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# device/recall fields RecallRecord carries; every other openFDA field is ignored.
_OPENFDA_FIELDS = (
    "product_res_number",
    "res_event_number",
    "k_numbers",
    "product_description",
    "root_cause_description",
    "reason_for_recall",
    "recall_status",
    "event_date_initiated",
    "event_date_posted",
    "event_date_created",
    "event_date_terminated",
)

# ADR-013: where a recall's availability date came from, in fallback order; "none" means no valid date.
# openFDA publishes the posting date as event_date_created for 13,575 records (2015-2022).
AVAILABILITY_SOURCES = (
    "recall.event_date_posted",
    "recall.event_date_created",
    "enforcement.report_date",
    "enforcement.center_classification_date",
    "none",
)
INITIATION_SOURCES = ("recall.event_date_initiated", "enforcement.recall_initiation_date")
# availability_upper_bound: the availability date is a back-fill entry date, so the recall was public
# on or before it (ADR-013 Revision 2); features counting recent recalls must skip it.
DATE_FLAGS = ("century_repaired", "initiation_conflict", "initiation_invalid", "posted_before_initiated", "availability_upper_bound")
# A century repair is confirmed and an upper bound never leaks, so only these keep a recall out of durations.
DURATION_EXCLUDING_FLAGS = frozenset({"initiation_conflict", "initiation_invalid", "posted_before_initiated"})


@dataclass
class RecallRecord:
    """Represents one recalled product from openFDA device/recall, linked to 510(k) devices."""
    product_res_number: str
    res_event_number: str
    classification: str | None = None  # Class I/II/III; device/recall has none, filled from enforcement (#129)
    k_numbers: list[str] = field(default_factory=list)
    product_description: str | None = None
    root_cause_description: str | None = None
    reason_for_recall: str | None = None
    recall_status: str | None = None
    event_date_initiated: str | None = None
    event_date_posted: str | None = None
    event_date_created: str | None = None  # openFDA's name for the posting date on 13,575 records
    event_date_terminated: str | None = None
    is_design_related: bool = False
    availability_date: str | None = None  # ISO date the recall counts as known; resolved per ADR-013 (#142)
    availability_source: str | None = None  # one of AVAILABILITY_SOURCES; None until resolved
    initiation_date_source: str | None = None  # one of INITIATION_SOURCES; None until resolved
    date_flags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalizes identifiers and K-numbers, rejects blank keys and inconsistent dates, and sets the design flag.

        Raises:
            ValueError: If product_res_number or res_event_number is missing or blank, or an
                ADR-013 date field holds an unknown value or a date that disagrees with its source.
        """
        for name in ("product_res_number", "res_event_number"):
            value = getattr(self, name)
            if value is None or not str(value).strip():
                raise ValueError(f"RecallRecord requires a non-blank {name}")
            setattr(self, name, str(value).strip())
        self.classification = (self.classification or "").strip() or None
        self.k_numbers = [k.strip().upper() for k in self.k_numbers if k]
        self._check_dates()

        text_check = f"{self.root_cause_description or ''} {self.reason_for_recall or ''}".lower()
        design_keywords = ["design", "software", "component design", "mechanical design", "hardware design"]
        if any(kw in text_check for kw in design_keywords):
            self.is_design_related = True

    def _check_dates(self) -> None:
        """Rejects ADR-013 date fields with unknown values or a date that disagrees with its source.

        Raises:
            ValueError: If a source or flag is unknown, or availability_date is set without a real
                source or missing with one.
        """
        if self.availability_source is not None and self.availability_source not in AVAILABILITY_SOURCES:
            raise ValueError(f"Unknown availability_source {self.availability_source!r}")
        if self.initiation_date_source is not None and self.initiation_date_source not in INITIATION_SOURCES:
            raise ValueError(f"Unknown initiation_date_source {self.initiation_date_source!r}")
        if unknown := [flag for flag in self.date_flags if flag not in DATE_FLAGS]:
            raise ValueError(f"Unknown date flags {unknown}")
        dated_source = self.availability_source not in (None, "none")
        if (self.availability_date is not None) != dated_source:
            raise ValueError(
                f"availability_date {self.availability_date!r} disagrees with availability_source {self.availability_source!r}"
            )

    @property
    def excluded_from_durations(self) -> bool:
        """Whether a date flag keeps this recall out of time-to-event durations (ADR-013).

        Returns:
            bool: True if any flag other than century_repaired is set.
        """
        return any(flag in DURATION_EXCLUDING_FLAGS for flag in self.date_flags)

    def to_dict(self) -> dict[str, Any]:
        """Converts the record to a plain dict.

        Returns:
            dict[str, Any]: Every contract field by name.
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecallRecord:
        """Builds a record from a dict produced by to_dict.

        Args:
            data: Contract fields by name; unknown keys raise TypeError.

        Returns:
            RecallRecord: The rebuilt record.
        """
        return cls(**data)

    @classmethod
    def from_openfda(cls, record: dict[str, Any]) -> RecallRecord:
        """Parses one raw openFDA device/recall record, ignoring fields the contract does not carry.

        Args:
            record: A device/recall result as returned by the API or bulk export.

        Returns:
            RecallRecord: The parsed record, with classification left None.

        Raises:
            ValueError: If product_res_number or res_event_number is missing or blank.
        """
        values = {name: record.get(name) for name in _OPENFDA_FIELDS}
        values["k_numbers"] = values["k_numbers"] or []
        return cls(**values)
