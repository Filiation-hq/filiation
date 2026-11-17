"""Unit tests for OpenFDAClient record parsing, with the HTTP layer replaced by fixed responses."""

from __future__ import annotations

import json
from pathlib import Path

from recension.pipeline.crawler.openfda_client import OpenFDAClient

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "openfda" / "recall_record_Z-0001-04.json"


def test_fetch_recalls_parses_real_openfda_records_without_inventing_a_class(tmp_path: Path, monkeypatch) -> None:
    # Sample: an API page holding the real device/recall record Z-0001-04. The old client set
    # classification to product_res_number ("Z-0001-04") and read status from a missing key.
    page = {"meta": {}, "results": [json.loads(FIXTURE.read_text())]}
    client = OpenFDAClient(cache_dir=tmp_path)
    monkeypatch.setattr(client, "_make_request", lambda endpoint, params: page)

    [rec] = client.fetch_recalls(limit=1)

    assert rec.product_res_number == "Z-0001-04"
    assert rec.classification is None
    assert rec.recall_status == "Terminated"
