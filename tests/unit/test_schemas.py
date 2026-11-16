"""Unit tests for Recension core schemas."""

import unittest

from recension.core.schemas.device_nodes import DeviceNode
from recension.core.schemas.predicate_edges import (
    ExtractionMethod,
    PredicateEdge,
    RelationshipType,
)
from recension.core.schemas.recalls import RecallRecord


class TestSchemas(unittest.TestCase):

    def test_device_node_normalization(self):
        node = DeviceNode(
            k_number=" k210758 ",
            applicant="Stryker Orthopaedics",
            device_name="Spine Nav",
            product_code=" mrn ",
            decision_date="2021-04-15",
            review_panel=" or ",
        )
        self.assertEqual(node.k_number, "K210758")
        self.assertEqual(node.product_code, "MRN")
        self.assertEqual(node.review_panel, "OR")
        d = node.to_dict()
        self.assertEqual(d["k_number"], "K210758")

    def test_predicate_edge_confidence_and_enums(self):
        edge = PredicateEdge(
            device_k="k210758",
            predicate_k="k180999",
            relationship_type="PRIMARY_PREDICATE",
            confidence_score=1.5,  # Exceeds 1.0, should clamp
            extraction_method="DIGITAL_REGEX",
        )
        self.assertEqual(edge.device_k, "K210758")
        self.assertEqual(edge.predicate_k, "K180999")
        self.assertEqual(edge.relationship_type, RelationshipType.PRIMARY_PREDICATE)
        self.assertEqual(edge.extraction_method, ExtractionMethod.DIGITAL_REGEX)
        self.assertEqual(edge.confidence_score, 1.0)

    def test_recall_record_design_defect_flag(self):
        rec_design = RecallRecord(
            res_event_number="90001",
            product_res_number="Z-1001-2023",
            classification="Class I",
            k_numbers=["K180999"],
            root_cause_description="Software design flaw causing battery drain",
        )
        self.assertTrue(rec_design.is_design_related)

        rec_packaging = RecallRecord(
            res_event_number="90002",
            product_res_number="Z-1002-2023",
            classification="Class II",
            k_numbers=["K180999"],
            root_cause_description="Packaging seal integrity compromised during transport",
        )
        self.assertFalse(rec_packaging.is_design_related)


if __name__ == "__main__":
    unittest.main()
