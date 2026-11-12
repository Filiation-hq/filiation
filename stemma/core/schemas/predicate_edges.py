"""Data contract and schema for predicate lineage edges."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class RelationshipType(str, Enum):
    """Categorization of device relationship in 510(k) summary."""
    PRIMARY_PREDICATE = "PRIMARY_PREDICATE"
    REFERENCE_DEVICE = "REFERENCE_DEVICE"
    MULTIPLE_PREDICATE = "MULTIPLE_PREDICATE"
    PRIOR_VERSION = "PRIOR_VERSION"
    UNKNOWN = "UNKNOWN"


class ExtractionMethod(str, Enum):
    """Method used to extract predicate citation."""
    DIGITAL_REGEX = "DIGITAL_REGEX"
    OCR_REGEX = "OCR_REGEX"
    LLM_FALLBACK = "LLM_FALLBACK"
    MANUAL_AUDIT = "MANUAL_AUDIT"


@dataclass
class PredicateEdge:
    """Represents a directed link from a subject device to its predicate or reference ancestor."""
    device_k: str
    predicate_k: str
    relationship_type: RelationshipType = RelationshipType.PRIMARY_PREDICATE
    source_page_num: Optional[int] = None
    confidence_score: float = 1.0
    extraction_method: ExtractionMethod = ExtractionMethod.DIGITAL_REGEX
    raw_text_snippet: Optional[str] = None
    section_heading: Optional[str] = None
    extracted_at: str = ""

    def __post_init__(self) -> None:
        if not self.extracted_at:
            self.extracted_at = datetime.now(timezone.utc).isoformat()
        if isinstance(self.relationship_type, str) and not isinstance(self.relationship_type, RelationshipType):
            self.relationship_type = RelationshipType(self.relationship_type)
        if isinstance(self.extraction_method, str) and not isinstance(self.extraction_method, ExtractionMethod):
            self.extraction_method = ExtractionMethod(self.extraction_method)
        # Normalize K-numbers
        self.device_k = self.device_k.strip().upper()
        self.predicate_k = self.predicate_k.strip().upper()
        # Clamp confidence score
        self.confidence_score = max(0.0, min(1.0, float(self.confidence_score)))

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["relationship_type"] = self.relationship_type.value
        data["extraction_method"] = self.extraction_method.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PredicateEdge:
        cleaned = dict(data)
        if "relationship_type" in cleaned:
            cleaned["relationship_type"] = RelationshipType(cleaned["relationship_type"])
        if "extraction_method" in cleaned:
            cleaned["extraction_method"] = ExtractionMethod(cleaned["extraction_method"])
        return cls(**cleaned)
