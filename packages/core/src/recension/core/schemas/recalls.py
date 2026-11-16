"""Data contract and schema for FDA medical device recall events (ADR-012)."""

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
)


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
    is_design_related: bool = False

    def __post_init__(self) -> None:
        """Normalizes identifiers and K-numbers, rejects blank keys, and sets the design flag.

        Raises:
            ValueError: If product_res_number or res_event_number is missing or blank.
        """
        for name in ("product_res_number", "res_event_number"):
            value = getattr(self, name)
            if value is None or not str(value).strip():
                raise ValueError(f"RecallRecord requires a non-blank {name}")
            setattr(self, name, str(value).strip())
        self.classification = (self.classification or "").strip() or None
        self.k_numbers = [k.strip().upper() for k in self.k_numbers if k]

        text_check = f"{self.root_cause_description or ''} {self.reason_for_recall or ''}".lower()
        design_keywords = ["design", "software", "component design", "mechanical design", "hardware design"]
        if any(kw in text_check for kw in design_keywords):
            self.is_design_related = True

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
