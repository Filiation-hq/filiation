"""Client for downloading openFDA bulk datasets and API queries."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from stemma.core.schemas.device_nodes import DeviceNode
from stemma.core.schemas.recalls import RecallRecord
from stemma.core.utils.paths import get_raw_dir

logger = logging.getLogger(__name__)


class OpenFDAClient:
    """Interface to openFDA 510(k) and Device Recall endpoints."""

    BASE_URL = "https://api.fda.gov/device"

    def __init__(self, api_key: Optional[str] = None, cache_dir: Optional[Path] = None):
        self.api_key = api_key
        self.cache_dir = cache_dir or (get_raw_dir() / "openfda")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_request(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Performs HTTP GET against openFDA with rate-limit respect."""
        if self.api_key:
            params["api_key"] = self.api_key

        url = f"{self.BASE_URL}/{endpoint}?{urlencode(params)}"
        req = Request(url, headers={"User-Agent": "Stemma/0.1.0 (device-lineage-research)"})

        for attempt in range(3):
            try:
                with urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as e:
                if e.code == 429:
                    wait_time = 2.0 * (attempt + 1)
                    logger.warning("openFDA rate limit hit. Sleeping %.1fs...", wait_time)
                    time.sleep(wait_time)
                elif e.code == 404:
                    return {"results": [], "meta": {"results": {"total": 0}}}
                else:
                    logger.error("HTTP error %d fetching %s: %s", e.code, url, e.reason)
                    raise
            except URLError as e:
                logger.warning("Network error on attempt %d: %s", attempt + 1, e.reason)
                time.sleep(1.0)

        raise RuntimeError(f"Failed to fetch {url} after 3 attempts.")

    def fetch_510k_records(self, limit: int = 100, skip: int = 0, search: Optional[str] = None) -> List[DeviceNode]:
        """Fetches 510(k) clearances and parses into DeviceNode instances."""
        params: Dict[str, Any] = {"limit": min(limit, 100), "skip": skip}
        if search:
            params["search"] = search

        data = self._make_request("510k.json", params)
        records: List[DeviceNode] = []
        for item in data.get("results", []):
            records.append(
                DeviceNode(
                    k_number=item.get("k_number", ""),
                    applicant=item.get("applicant", ""),
                    device_name=item.get("device_name", ""),
                    product_code=item.get("product_code", ""),
                    decision_date=item.get("decision_date", ""),
                    review_panel=item.get("advisory_committee", ""),
                    statement_or_summary=item.get("statement_or_summary", "Summary"),
                    expedited_review_flag=item.get("expedited_review_flag"),
                    third_party_flag=item.get("third_party_flag"),
                )
            )
        return records

    def fetch_recalls(self, limit: int = 100, skip: int = 0, search: Optional[str] = None) -> List[RecallRecord]:
        """Fetches device recalls and parses into RecallRecord instances."""
        params: Dict[str, Any] = {"limit": min(limit, 100), "skip": skip}
        if search:
            params["search"] = search

        data = self._make_request("recall.json", params)
        records: List[RecallRecord] = []
        for item in data.get("results", []):
            records.append(
                RecallRecord(
                    res_event_number=item.get("res_event_number", ""),
                    recall_number=item.get("recall_number", ""),
                    classification=item.get("product_res_number", item.get("root_cause_description", "Class II")),
                    k_numbers=item.get("k_numbers", []),
                    product_description=item.get("product_description"),
                    root_cause_description=item.get("root_cause_description"),
                    reason_for_recall=item.get("reason_for_recall"),
                    recall_status=item.get("status"),
                    event_date_initiated=item.get("event_date_initiated"),
                    event_date_posted=item.get("event_date_posted"),
                )
            )
        return records
