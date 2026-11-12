"""Data contract and schema for FDA medical device recall events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RecallRecord:
    """Represents an FDA recall action linked to 510(k) devices."""
    res_event_number: str
    recall_number: str
    classification: str  # Class I, Class II, Class III
    k_numbers: List[str] = field(default_factory=list)
    product_description: Optional[str] = None
    root_cause_description: Optional[str] = None
    reason_for_recall: Optional[str] = None
    recall_status: Optional[str] = None
    event_date_initiated: Optional[str] = None
    event_date_posted: Optional[str] = None
    is_design_related: bool = False

    def __post_init__(self) -> None:
        self.res_event_number = str(self.res_event_number).strip()
        self.recall_number = str(self.recall_number).strip()
        self.classification = self.classification.strip()
        self.k_numbers = [k.strip().upper() for k in self.k_numbers if k]
        
        # Check if root cause indicates design defect
        text_check = f"{self.root_cause_description or ''} {self.reason_for_recall or ''}".lower()
        design_keywords = ["design", "software", "component design", "mechanical design", "hardware design"]
        if any(kw in text_check for kw in design_keywords):
            self.is_design_related = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RecallRecord:
        return cls(**data)
