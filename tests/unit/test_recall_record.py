"""Unit tests for parsing openFDA device/recall records into RecallRecord (#77, ADR-012).

Input: one raw openFDA device/recall record (a dict straight from the API or bulk export).
Output: a RecallRecord keyed on product_res_number, with res_event_number, k_numbers, dates
and status copied over, and classification left None (device/recall has no recall class).

Edge cases:
- Real record with ~20 fields the contract does not carry -> extra fields ignored.
- product_res_number missing -> ValueError naming the field.
- res_event_number blank -> ValueError naming the field.
- k_numbers absent or null (about a third of records) -> empty list.
- event_date_posted absent (13,575 records) -> None.
- Status lives in recall_status, not status -> read from recall_status.
- Blank classification string -> None, never "".
- to_dict / from_dict round trip -> equal record.

ADR-013 date fields (#142): availability_date, availability_source, initiation_date_source,
date_flags. Edge cases: unresolved by default; unknown source or flag -> ValueError; a date
without a real source, or a real source without a date -> ValueError; which flags exclude a
recall from durations; round trip; DDL columns. event_date_created (the posted date's name for
13,575 records, 2015-2022) and event_date_terminated are carried so the rules can read them.

Invariants: no field is invented (classification is never guessed); keys are stripped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from recension.core.schemas.data_contract import (
    CREATE_TABLE_DEVICE_RECALL_LINKS,
    CREATE_TABLE_RECALLS,
)
from recension.core.schemas.recalls import AVAILABILITY_SOURCES, DATE_FLAGS, RecallRecord

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "openfda" / "recall_record_Z-0001-04.json"


def real_record() -> dict:
    """Loads the real openFDA device/recall record Z-0001-04.

    Returns:
        dict: A fresh copy of the record, safe to mutate.
    """
    return json.loads(FIXTURE.read_text())


def test_from_openfda_parses_a_real_recall_record_keyed_on_product_res_number() -> None:
    # Sample: real record Z-0001-04 (event 27549, K020436), copied from the openFDA API on
    # 2026-11-16 with the contact person removed. It has no recall_number field at all.
    rec = RecallRecord.from_openfda(real_record())
    assert rec.product_res_number == "Z-0001-04"
    assert rec.res_event_number == "27549"
    assert rec.k_numbers == ["K020436"]
    assert rec.event_date_initiated == "2003-10-27"
    assert rec.event_date_posted == "2003-10-29"


def test_from_openfda_ignores_fields_the_contract_does_not_carry() -> None:
    # Sample: the same real record, which also carries cfres_id, code_info, recalling_firm,
    # firm_fei_number and others. Passing them to the constructor used to raise TypeError.
    rec = RecallRecord.from_openfda(real_record())
    assert "cfres_id" not in rec.to_dict()
    assert "recalling_firm" not in rec.to_dict()


def test_from_openfda_leaves_classification_none_because_device_recall_has_no_class() -> None:
    # Sample: the real record. device/recall never has classification (0 of 59,705 records),
    # so the old client's guess (product_res_number as the class) must not come back.
    assert RecallRecord.from_openfda(real_record()).classification is None


def test_from_openfda_rejects_a_record_without_product_res_number() -> None:
    # Sample: the real record with product_res_number removed, so it has no record key.
    record = real_record()
    del record["product_res_number"]
    with pytest.raises(ValueError, match="product_res_number"):
        RecallRecord.from_openfda(record)


def test_from_openfda_rejects_a_blank_res_event_number() -> None:
    # Sample: the real record with res_event_number set to whitespace, so the event is unknown.
    record = real_record()
    record["res_event_number"] = "  "
    with pytest.raises(ValueError, match="res_event_number"):
        RecallRecord.from_openfda(record)


def test_from_openfda_reads_an_absent_k_numbers_field_as_an_empty_list() -> None:
    # Sample: the real record without k_numbers; about 21k of 59,705 records have none.
    record = real_record()
    del record["k_numbers"]
    assert RecallRecord.from_openfda(record).k_numbers == []


def test_from_openfda_reads_a_null_k_numbers_field_as_an_empty_list() -> None:
    # Sample: the real record with k_numbers set to JSON null instead of a list.
    record = real_record()
    record["k_numbers"] = None
    assert RecallRecord.from_openfda(record).k_numbers == []


def test_from_openfda_reads_an_absent_event_date_posted_as_none() -> None:
    # Sample: the real record without event_date_posted, as in 13,575 openFDA records.
    record = real_record()
    del record["event_date_posted"]
    assert RecallRecord.from_openfda(record).event_date_posted is None


def test_from_openfda_reads_event_date_created_and_event_date_terminated() -> None:
    # Sample: the real record Z-0001-04 (terminated 2004-03-24, no event_date_created), and the
    # same record with posted renamed to created, as openFDA publishes Z-0494-05 and 13,574 others.
    record = real_record()
    assert (RecallRecord.from_openfda(record).event_date_created, RecallRecord.from_openfda(record).event_date_terminated) == (None, "2004-03-24")
    record["event_date_created"] = record.pop("event_date_posted")
    parsed = RecallRecord.from_openfda(record)
    assert (parsed.event_date_posted, parsed.event_date_created) == (None, "2003-10-29")


def test_from_openfda_reads_recall_status_from_the_recall_status_field() -> None:
    # Sample: the real record, whose status is in recall_status ("Terminated"); the old
    # client read a "status" key that openFDA does not have.
    assert RecallRecord.from_openfda(real_record()).recall_status == "Terminated"


def test_recall_record_treats_a_blank_classification_as_none() -> None:
    # Sample: a record built with classification "  ", as an empty enforcement field would give.
    rec = RecallRecord(product_res_number="Z-0001-04", res_event_number="27549", classification="  ")
    assert rec.classification is None


def test_recall_record_round_trips_through_to_dict_and_from_dict() -> None:
    # Sample: the parsed real record, written to a dict and read back.
    rec = RecallRecord.from_openfda(real_record())
    assert RecallRecord.from_dict(rec.to_dict()) == rec


def test_recalls_ddl_keys_on_product_res_number_and_allows_a_null_classification() -> None:
    # Sample: the DuckDB DDL strings for recalls and device_recall_links. Both used to key on
    # recall_number and declare classification NOT NULL, which real data cannot satisfy.
    assert "product_res_number VARCHAR PRIMARY KEY" in CREATE_TABLE_RECALLS
    assert "PRIMARY KEY (k_number, product_res_number)" in CREATE_TABLE_DEVICE_RECALL_LINKS
    for ddl in (CREATE_TABLE_RECALLS, CREATE_TABLE_DEVICE_RECALL_LINKS):
        assert "recall_number" not in ddl.replace("product_res_number", "")
        assert "classification VARCHAR NOT NULL" not in ddl


def resolved(**dates) -> RecallRecord:
    """Builds the real record Z-0001-04 with the given ADR-013 date fields.

    Args:
        **dates: Any of availability_date, availability_source, initiation_date_source, date_flags.

    Returns:
        RecallRecord: The record with those fields set.
    """
    return RecallRecord(**{**RecallRecord.from_openfda(real_record()).to_dict(), **dates})


def test_from_openfda_leaves_availability_unresolved_until_adr_013_rules_run() -> None:
    # Sample: the raw record Z-0001-04. Parsing alone must not pick an availability date.
    rec = RecallRecord.from_openfda(real_record())
    assert (rec.availability_date, rec.availability_source, rec.initiation_date_source, rec.date_flags) == (None, None, None, [])


def test_recall_record_rejects_an_unknown_availability_source() -> None:
    # Sample: "recall.event_date_initiated" as a source; ADR-013 forbids dating from initiation.
    with pytest.raises(ValueError, match="availability_source"):
        resolved(availability_date="2004-01-20", availability_source="recall.event_date_initiated")


def test_recall_record_rejects_an_unknown_date_flag() -> None:
    # Sample: a misspelled flag "century_fixed" instead of "century_repaired".
    with pytest.raises(ValueError, match="century_fixed"):
        resolved(date_flags=["century_fixed"])


@pytest.mark.parametrize(
    "dates",
    [
        {"availability_date": "2004-01-20", "availability_source": "none"},
        {"availability_date": None, "availability_source": "enforcement.report_date"},
        {"availability_date": "2004-01-20", "availability_source": None},
    ],
)
def test_recall_record_rejects_an_availability_date_that_disagrees_with_its_source(dates) -> None:
    # Sample: a date with source "none", a real source with no date, and a date with no source.
    with pytest.raises(ValueError, match="availability"):
        resolved(**dates)


@pytest.mark.parametrize(
    ("flags", "excluded"),
    [
        ([], False),
        (["century_repaired"], False),
        (["initiation_conflict"], True),
        (["initiation_invalid"], True),
        (["posted_before_initiated"], True),
    ],
)
def test_recall_record_excludes_conflicting_invalid_or_inverted_dates_from_durations(flags, excluded) -> None:
    # Sample: one record per ADR-013 flag. A confirmed century repair stays usable; the others don't.
    assert resolved(date_flags=flags).excluded_from_durations is excluded


def test_recall_record_round_trips_resolved_dates_through_to_dict_and_from_dict() -> None:
    # Sample: Z-1044-2013's resolution: availability 2013-04-02 from the posted date, initiation
    # repaired from enforcement (raw "0013-03-05").
    rec = resolved(
        availability_date="2013-04-02",
        availability_source="recall.event_date_posted",
        initiation_date_source="enforcement.recall_initiation_date",
        date_flags=["century_repaired"],
    )
    assert RecallRecord.from_dict(rec.to_dict()) == rec


def test_adr_013_vocabularies_list_every_source_and_flag() -> None:
    # Sample: the allowed values ADR-013 names; label code and reports rely on these strings.
    assert AVAILABILITY_SOURCES == (
        "recall.event_date_posted", "recall.event_date_created", "enforcement.report_date",
        "enforcement.center_classification_date", "none",
    )
    assert DATE_FLAGS == ("century_repaired", "initiation_conflict", "initiation_invalid", "posted_before_initiated")


def test_recalls_ddl_has_the_adr_013_date_columns() -> None:
    # Sample: the recalls DDL string; a derived table must be able to store resolved dates.
    for column in ("event_date_created DATE", "event_date_terminated DATE", "availability_date DATE", "availability_source VARCHAR", "initiation_date_source VARCHAR", "date_flags VARCHAR[]"):
        assert column in CREATE_TABLE_RECALLS
