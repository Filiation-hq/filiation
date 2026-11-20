"""Data contract and schema for FDA medical device recall events and the recall label (ADR-012, ADR-013, ADR-017)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

# device/recall fields RecallRecord carries; every other openFDA field is ignored.
_OPENFDA_FIELDS = (
    "product_res_number",
    "res_event_number",
    "k_numbers",
    "pma_numbers",
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
# FDA recall classes, most severe first; a label takes the most severe class among its products (ADR-017).
CLASS_SEVERITY = ("Class I", "Class II", "Class III")
# Approval pathways a recall label can belong to; only 510k labels join the predicate graph (ADR-017).
PATHWAYS = ("510k", "pma")
_PMA_NUMBER = re.compile(r"P\d{6}")


@dataclass
class RecallRecord:
    """Represents one recalled product from openFDA device/recall, linked to 510(k) devices."""
    product_res_number: str
    res_event_number: str
    classification: str | None = None  # Class I/II/III; device/recall has none, filled from enforcement (#129)
    k_numbers: list[str] = field(default_factory=list)
    pma_numbers: list[str] = field(default_factory=list)  # base P-numbers; unparseable values kept as written
    product_description: str | None = None
    root_cause_description: str | None = None
    reason_for_recall: str | None = None
    recall_status: str | None = None  # this snapshot's state; changes after posting, never a label (ADR-017)
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
        """Normalizes identifiers, K-numbers and PMA numbers, rejects blank keys and inconsistent dates, and sets the design flag.

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
        self.pma_numbers = list(dict.fromkeys(normalize_pma_number(p) for p in self.pma_numbers if p and p.strip()))
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
        _check_availability(self.availability_date, self.availability_source)
        if self.initiation_date_source is not None and self.initiation_date_source not in INITIATION_SOURCES:
            raise ValueError(f"Unknown initiation_date_source {self.initiation_date_source!r}")
        if unknown := [flag for flag in self.date_flags if flag not in DATE_FLAGS]:
            raise ValueError(f"Unknown date flags {unknown}")

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
        values["pma_numbers"] = values["pma_numbers"] or []
        return cls(**values)


def _check_availability(availability_date: str | None, availability_source: str | None) -> None:
    """Rejects an unknown availability source or a date that disagrees with its source (ADR-013).

    Args:
        availability_date: ISO date the recall counts as known, or None.
        availability_source: One of AVAILABILITY_SOURCES, or None while unresolved.

    Raises:
        ValueError: If the source is unknown, or the date is set without a real source or missing with one.
    """
    if availability_source is not None and availability_source not in AVAILABILITY_SOURCES:
        raise ValueError(f"Unknown availability_source {availability_source!r}")
    if (availability_date is not None) != (availability_source not in (None, "none")):
        raise ValueError(f"availability_date {availability_date!r} disagrees with availability_source {availability_source!r}")


@dataclass(frozen=True)
class DeviceRecallEvent:
    """One recall event as it touches one device application (510(k) or PMA), the unit of the recall label (ADR-017)."""

    pathway: str
    application_number: str
    res_event_number: str
    product_res_numbers: tuple[str, ...]
    classification: str | None = None
    availability_date: str | None = None
    availability_source: str | None = None
    event_date_initiated: str | None = None
    is_design_related: bool = False
    date_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Rejects blank keys, an unknown pathway or malformed PMA number, no products, an unknown class or flag, and inconsistent availability.

        Raises:
            ValueError: If any field breaks the label contract.
        """
        for name in ("application_number", "res_event_number"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"DeviceRecallEvent requires a non-blank {name}")
        if self.pathway not in PATHWAYS:
            raise ValueError(f"Unknown pathway {self.pathway!r}")
        if self.pathway == "pma" and not _PMA_NUMBER.fullmatch(self.application_number):
            raise ValueError(f"PMA label needs a base P-number, not {self.application_number!r}")
        if not self.product_res_numbers:
            raise ValueError(f"DeviceRecallEvent {self.application_number}/{self.res_event_number} has no products")
        if self.classification is not None and self.classification not in CLASS_SEVERITY:
            raise ValueError(f"Unknown recall class {self.classification!r}")
        if unknown := [flag for flag in self.date_flags if flag not in DATE_FLAGS]:
            raise ValueError(f"Unknown date flags {unknown}")
        _check_availability(self.availability_date, self.availability_source)

    def known_by(self, as_of: date) -> bool:
        """Tells whether the recall was public on a given day, by its availability date only.

        Args:
            as_of: The cutoff day; the availability date itself counts as known.

        Returns:
            bool: True if the label has an availability date on or before as_of.
        """
        return self.availability_date is not None and date.fromisoformat(self.availability_date) <= as_of

    @property
    def excluded_from_durations(self) -> bool:
        """Whether a date flag keeps this label out of time-to-event durations (ADR-013).

        Returns:
            bool: True if any flag other than century_repaired or availability_upper_bound is set.
        """
        return any(flag in DURATION_EXCLUDING_FLAGS for flag in self.date_flags)

    @classmethod
    def from_recalls(cls, pathway: str, application_number: str, recalls: Iterable[RecallRecord]) -> DeviceRecallEvent:
        """Combines the products of one recall event that name one device application into its label.

        Args:
            pathway: "510k" or "pma"; selects whether products must name it as a K-number or a PMA number.
            application_number: The K-number or base PMA number; every product must name it.
            recalls: The event's products, with ADR-013 availability resolved for all or none.

        Returns:
            DeviceRecallEvent: The most severe class, the earliest availability date, the earliest
                initiation, any design fault, and every date flag of the products.

        Raises:
            ValueError: If there are no products, they span events, one does not name k_number,
                or availability is resolved for only some of them.
        """
        products = list(recalls)
        if not products:
            raise ValueError(f"No products to label for {application_number}")
        event = products[0].res_event_number
        for rec in products:
            if rec.res_event_number != event:
                raise ValueError(f"{rec.product_res_number} belongs to event {rec.res_event_number}, not {event}")
            if application_number not in (rec.pma_numbers if pathway == "pma" else rec.k_numbers):
                raise ValueError(f"{rec.product_res_number} does not name {application_number}")
        resolved = [rec.availability_source is not None for rec in products]
        if any(resolved) and not all(resolved):
            raise ValueError(f"Event {event}: resolve availability for every product before labeling")
        classes = [rec.classification for rec in products if rec.classification in CLASS_SEVERITY]
        dated = sorted(
            (rec.availability_date, AVAILABILITY_SOURCES.index(rec.availability_source or "none"), rec.availability_source)
            for rec in products
            if rec.availability_date is not None
        )
        initiated = sorted(day for rec in products if (day := _iso_or_none(rec.event_date_initiated)))
        return cls(
            pathway=pathway,
            application_number=application_number,
            res_event_number=event,
            product_res_numbers=tuple(sorted({rec.product_res_number for rec in products})),
            classification=min(classes, key=CLASS_SEVERITY.index) if classes else None,
            availability_date=dated[0][0] if dated else None,
            availability_source=dated[0][2] if dated else ("none" if all(resolved) else None),
            event_date_initiated=initiated[0] if initiated else None,
            is_design_related=any(rec.is_design_related for rec in products),
            date_flags=tuple(sorted({flag for rec in products for flag in rec.date_flags})),
        )


def _iso_or_none(value: str | None) -> str | None:
    """Returns a value only if it is a valid ISO date, so mixed formats cannot sort wrongly.

    Args:
        value: A date string, or None.

    Returns:
        str | None: The value if date.fromisoformat accepts it as YYYY-MM-DD, else None.
    """
    if not value or len(value) != 10:
        return None
    try:
        date.fromisoformat(value)
    except ValueError:
        return None
    return value


def device_recall_events(recalls: Iterable[RecallRecord]) -> list[DeviceRecallEvent]:
    """Groups one snapshot's recalled products into recall labels, one per device application and event (ADR-017).

    Args:
        recalls: Recalled products; one naming no K-number or base PMA number yields no label.

    Returns:
        list[DeviceRecallEvent]: Labels sorted by (pathway, application_number, res_event_number).

    Raises:
        ValueError: If one product_res_number appears with different content, or a label's
            products break DeviceRecallEvent.from_recalls.
    """
    by_product: dict[str, RecallRecord] = {}
    for rec in recalls:
        seen = by_product.setdefault(rec.product_res_number, rec)
        if seen != rec:
            raise ValueError(f"Product {rec.product_res_number} appears twice with different content")
    groups: dict[tuple[str, str, str], list[RecallRecord]] = {}
    for rec in by_product.values():
        names = [("510k", k) for k in dict.fromkeys(rec.k_numbers) if k]
        names += [("pma", p) for p in rec.pma_numbers if _PMA_NUMBER.fullmatch(p)]
        for pathway, number in names:
            groups.setdefault((pathway, number, rec.res_event_number), []).append(rec)
    return [DeviceRecallEvent.from_recalls(pathway, number, products) for (pathway, number, _), products in sorted(groups.items())]


def normalize_pma_number(raw: str) -> str:
    """Reduces a PMA number as openFDA writes it to its base P-number, so it matches FDA's PMA export.

    Args:
        raw: e.g. "P850020S011", "P050052/SO52" or "PMA-P900023".

    Returns:
        str: The base number (e.g. "P850020"), or the stripped, upper-cased value if it holds none.
    """
    value = raw.strip().upper()
    match = _PMA_NUMBER.search(value)
    return match.group(0) if match else value
