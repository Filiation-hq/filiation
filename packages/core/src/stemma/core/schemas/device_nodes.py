"""Data contract and schema for 510(k) clearance device nodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class DeviceNode:
    """Represents a cleared medical device node in the lineage graph."""
    k_number: str
    applicant: str
    device_name: str
    product_code: str
    decision_date: str
    review_panel: Optional[str] = None
    statement_or_summary: Optional[str] = "Summary"
    expedited_review_flag: Optional[str] = None
    third_party_flag: Optional[str] = None

    def __post_init__(self) -> None:
        self.k_number = self.k_number.strip().upper()
        if self.product_code:
            self.product_code = self.product_code.strip().upper()
        if self.review_panel:
            self.review_panel = self.review_panel.strip().upper()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DeviceNode:
        return cls(**data)
