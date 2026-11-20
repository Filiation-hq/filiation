"""Unit tests for the recall label unit, DeviceRecallEvent, and how recalls group into it (#93, ADR-017).

Input: RecallRecords from one snapshot, each one recalled product naming zero or more K-numbers
and PMA numbers, with ADR-013 availability dates already resolved (or not yet resolved).
Output: one DeviceRecallEvent per (pathway, application_number, res_event_number), the unit of the
recall label: its products, the most severe class among them, the earliest availability date,
and whether the event was known by a given date.

Edge cases:
- One event naming the same K-number on several products (3,704 real events) -> one label.
- One product naming several K-numbers -> one label per K-number.
- Recall without K-numbers or PMA numbers, or with a blank K-number -> no label.
- PMA numbers with supplement suffixes or prefixes (P850020S011, P050052/SO52, PMA-P900023) -> base
  P-number, so 2,711 of 2,744 real PMA recalls match FDA's PMA export instead of 1,888.
- PMA value with no P-number -> kept as written on the record, no label.
- Product naming both a K-number and a PMA -> one label on each pathway.
- Unknown pathway, or a PMA label whose number is not a base P-number -> ValueError.
- Same product twice: identical -> collapsed; different -> ValueError.
- Input order -> output sorted by (k_number, res_event_number).
- Mixed classes in one event (6 real events) -> most severe; unclassified products ignored.
- Unknown class string -> ValueError.
- Products dated on different days -> earliest; tie -> source earliest in the ADR-013 order.
- No product dated -> availability None, source "none"; never resolved -> source None.
- Some products resolved and others not -> ValueError (resolution must run first).
- known_by: inclusive on the availability date; never known without one; never by initiation.
- recall_status and event_date_terminated change after posting -> not on the label.
- Blank keys, no products, a product from another event or not naming the number -> ValueError.

Invariants: the label never reads a field that changes after the recall was posted, and no
recall counts as known before its availability date.

New names are reached through the recalls module so the TDD guard can run these tests on base (#137).
"""

from __future__ import annotations

from datetime import date

import pytest
from filiation.core.schemas import data_contract
from filiation.core.schemas import recalls as r

POSTED = "recall.event_date_posted"
REPORTED = "enforcement.report_date"
CLASSIFIED = "enforcement.center_classification_date"


def product(z: str, event: str = "EV1", ks: tuple[str, ...] = ("K180999",), **fields) -> r.RecallRecord:
    """Builds one recalled product with the given K-numbers and contract fields.

    Args:
        z: product_res_number.
        event: res_event_number.
        ks: K-numbers the product names.
        **fields: Any other RecallRecord field.

    Returns:
        RecallRecord: The product.
    """
    return r.RecallRecord(product_res_number=z, res_event_number=event, k_numbers=list(ks), **fields)


def dated(z: str, day: str, source: str = POSTED, **fields) -> r.RecallRecord:
    """Builds one product of event EV1 for K180999 with a resolved availability date.

    Args:
        z: product_res_number.
        day: ISO availability date.
        source: availability_source that supplied it.
        **fields: Any other RecallRecord field.

    Returns:
        RecallRecord: The product.
    """
    return product(z, availability_date=day, availability_source=source, **fields)


def only(k: str, records: list[r.RecallRecord]) -> r.DeviceRecallEvent:
    """Builds the single 510(k) label for K-number k from records of one event.

    Args:
        k: K-number.
        records: Products of one event naming k.

    Returns:
        DeviceRecallEvent: The label.
    """
    return r.DeviceRecallEvent.from_recalls("510k", k, records)


def test_device_recall_events_give_one_label_per_k_number_and_event_when_products_repeat_a_k_number() -> None:
    # Sample: event EV1 recalls two products, Z-0001-20 and Z-0002-20, both naming K180999
    # (the shape of 3,704 real events). Counting products would count this recall twice.
    [label] = r.device_recall_events([product("Z-0001-20"), product("Z-0002-20")])
    assert (label.pathway, label.application_number, label.res_event_number, label.product_res_numbers) == (
        "510k",
        "K180999",
        "EV1",
        ("Z-0001-20", "Z-0002-20"),
    )


def test_device_recall_events_give_each_k_number_of_one_product_its_own_label() -> None:
    # Sample: one product naming two cleared devices, K180999 and K170001 (listed twice).
    labels = r.device_recall_events([product("Z-0001-20", ks=("K180999", "K170001", "K170001"))])
    assert [(x.application_number, x.product_res_numbers) for x in labels] == [
        ("K170001", ("Z-0001-20",)),
        ("K180999", ("Z-0001-20",)),
    ]


def test_device_recall_events_skip_recalls_without_k_numbers_or_with_blank_ones() -> None:
    # Sample: a 510(k)-exempt device recall naming no device number (8,179 real records) and
    # one whose only K-number is whitespace, which RecallRecord normalizes to "".
    assert r.device_recall_events([product("Z-0001-20", ks=()), product("Z-0002-20", ks=("  ",))]) == []


def test_device_recall_events_collapse_an_identical_duplicate_product() -> None:
    # Sample: Z-0001-20 passed twice with the same content, e.g. read from two partitions.
    [label] = r.device_recall_events([product("Z-0001-20"), product("Z-0001-20")])
    assert label.product_res_numbers == ("Z-0001-20",)


def test_device_recall_events_reject_one_product_key_with_two_different_records() -> None:
    # Sample: Z-0001-20 twice, once Class I and once Class II. One key cannot mean two recalls.
    with pytest.raises(ValueError, match="Z-0001-20"):
        r.device_recall_events([product("Z-0001-20", classification="Class I"), product("Z-0001-20", classification="Class II")])


def test_device_recall_events_come_out_sorted_by_pathway_number_and_event_regardless_of_input_order() -> None:
    # Sample: four labels fed in reverse order of (pathway, application_number, res_event_number),
    # one of them a PMA recall naming P910020.
    records = [
        product("Z-4", "EV1", (), pma_numbers=["P910020"]),
        product("Z-3", "EV2", ("K2",)),
        product("Z-2", "EV1", ("K2",)),
        product("Z-1", "EV9", ("K1",)),
    ]
    keys = [(x.pathway, x.application_number, x.res_event_number) for x in r.device_recall_events(records)]
    assert keys == [("510k", "K1", "EV9"), ("510k", "K2", "EV1"), ("510k", "K2", "EV2"), ("pma", "P910020", "EV1")]


def test_label_class_is_the_most_severe_class_among_its_products() -> None:
    # Sample: event 73283's shape, one product Class II and one Class I for the same device.
    label = only("K180999", [product("Z-1", classification="Class II"), product("Z-2", classification="Class I")])
    assert label.classification == "Class I"


def test_label_class_ignores_products_without_a_class() -> None:
    # Sample: one unclassified product (no enforcement match) and one Class III; then two
    # unclassified products, which leave the label's class unknown rather than guessed.
    assert only("K180999", [product("Z-1"), product("Z-2", classification="Class III")]).classification == "Class III"
    assert only("K180999", [product("Z-1"), product("Z-2")]).classification is None


def test_label_rejects_an_unknown_class_string() -> None:
    # Sample: "Class IV", which FDA does not use; a typo must not become a label.
    with pytest.raises(ValueError, match="Class IV"):
        r.DeviceRecallEvent("510k", "K180999", "EV1", ("Z-1",), classification="Class IV")


def test_label_availability_is_the_earliest_dated_product() -> None:
    # Sample: Z-2 available 2019-08-01 from enforcement and Z-1 posted 2019-08-20. The event
    # was public once any of its products was.
    label = only("K180999", [dated("Z-1", "2019-08-20"), dated("Z-2", "2019-08-01", REPORTED)])
    assert (label.availability_date, label.availability_source) == ("2019-08-01", REPORTED)


def test_label_availability_tie_takes_the_source_earliest_in_the_fallback_order() -> None:
    # Sample: both products available 2019-08-01, one by classification date, one by posting.
    # The ADR-013 order ranks the posting date first, whatever the input order.
    label = only("K180999", [dated("Z-1", "2019-08-01", CLASSIFIED), dated("Z-2", "2019-08-01")])
    assert label.availability_source == POSTED


def test_label_with_no_dated_product_has_no_availability_and_source_none() -> None:
    # Sample: two products whose ADR-013 resolution found no valid date (back-filled, no enforcement).
    label = only("K180999", [product("Z-1", availability_source="none"), product("Z-2", availability_source="none")])
    assert (label.availability_date, label.availability_source) == (None, "none")


def test_label_with_unresolved_products_has_no_availability_source() -> None:
    # Sample: products straight from device/recall, before ADR-013 resolution ran.
    label = only("K180999", [product("Z-1"), product("Z-2")])
    assert (label.availability_date, label.availability_source) == (None, None)


def test_label_rejects_products_with_partly_resolved_availability() -> None:
    # Sample: Z-1 resolved to 2019-08-01, Z-2 never resolved. Its date might be earlier, so
    # taking Z-1's would be a guess.
    with pytest.raises(ValueError, match="resolve"):
        only("K180999", [dated("Z-1", "2019-08-01"), product("Z-2")])


def test_label_is_known_on_its_availability_date_and_not_the_day_before() -> None:
    # Sample: a label available 2019-08-01, checked on 2019-07-31 and on 2019-08-01.
    label = only("K180999", [dated("Z-1", "2019-08-01")])
    assert (label.known_by(date(2019, 7, 31)), label.known_by(date(2019, 8, 1))) == (False, True)


def test_label_without_an_availability_date_is_never_known() -> None:
    # Sample: an undated label, checked a century later. ADR-013 never falls back to initiation.
    assert only("K180999", [product("Z-1", availability_source="none")]).known_by(date(2126, 1, 1)) is False


def test_label_is_not_known_from_its_initiation_date() -> None:
    # Sample: initiated 2019-06-15 but posted 2019-08-06 (the real median lag is 52 days).
    # On 2019-07-01 the firm had started the recall, but the public could not know it.
    label = only("K180999", [dated("Z-1", "2019-08-06", event_date_initiated="2019-06-15")])
    assert label.event_date_initiated == "2019-06-15"
    assert label.known_by(date(2019, 7, 1)) is False


def test_label_carries_no_recall_status_or_termination_date() -> None:
    # Sample: the label's field names. recall_status moves from "Open, Classified" to
    # "Terminated" after posting (44,691 real records are Terminated), and termination comes
    # later still, so neither may reach a label (R03).
    names = set(r.DeviceRecallEvent.__dataclass_fields__)
    assert names.isdisjoint({"recall_status", "event_date_terminated"})


def test_label_is_design_related_when_any_product_is() -> None:
    # Sample: one product recalled for a software design fault, one for a labeling error.
    records = [
        product("Z-1", reason_for_recall="Labeling error"),
        product("Z-2", root_cause_description="Software design"),
    ]
    assert only("K180999", records).is_design_related is True


def test_label_date_flags_are_the_union_and_exclude_from_durations() -> None:
    # Sample: Z-1 flagged century_repaired, Z-2 flagged posted_before_initiated; the label
    # keeps both, and the second keeps it out of time-to-event durations.
    records = [
        dated("Z-1", "2019-08-01", date_flags=["century_repaired"]),
        dated("Z-2", "2019-08-01", date_flags=["posted_before_initiated", "century_repaired"]),
    ]
    label = only("K180999", records)
    assert label.date_flags == ("century_repaired", "posted_before_initiated")
    assert label.excluded_from_durations is True


def test_label_initiation_is_the_earliest_product_initiation() -> None:
    # Sample: two products initiated 2019-06-20 and 2019-06-15 (real events never differ,
    # but nothing in the data forbids it), and one with no initiation date.
    records = [
        product("Z-1", event_date_initiated="2019-06-20"),
        product("Z-2", event_date_initiated="2019-06-15"),
        product("Z-3"),
    ]
    assert only("K180999", records).event_date_initiated == "2019-06-15"


@pytest.mark.parametrize(
    "fields",
    [
        {"pathway": "510k", "application_number": " ", "res_event_number": "EV1", "product_res_numbers": ("Z-1",)},
        {"pathway": "510k", "application_number": "K180999", "res_event_number": "", "product_res_numbers": ("Z-1",)},
        {"pathway": "510k", "application_number": "K180999", "res_event_number": "EV1", "product_res_numbers": ()},
        {"pathway": "de_novo", "application_number": "DEN170001", "res_event_number": "EV1", "product_res_numbers": ("Z-1",)},
        {"pathway": "pma", "application_number": "P850020S011", "res_event_number": "EV1", "product_res_numbers": ("Z-1",)},
    ],
)
def test_label_rejects_blank_keys_no_products_an_unknown_pathway_or_a_malformed_pma_number(fields) -> None:
    # Sample: a blank K-number, a blank event, a label with no products behind it, a pathway the
    # contract does not define, and a PMA label keyed on a supplement instead of its base P-number.
    with pytest.raises(ValueError):
        r.DeviceRecallEvent(**fields)


def test_label_rejects_a_product_from_another_event_or_not_naming_the_number() -> None:
    # Sample: building K180999's EV1 label from an EV2 product, then from a product naming only K170001.
    with pytest.raises(ValueError, match="EV2"):
        only("K180999", [product("Z-1"), product("Z-2", "EV2")])
    with pytest.raises(ValueError, match="K180999"):
        only("K180999", [product("Z-1", ks=("K170001",))])


def test_device_recall_links_ddl_stores_one_row_per_pathway_number_and_event_with_its_availability_date() -> None:
    # Sample: the DuckDB DDL for device_recall_links, which used to key on product_res_number
    # and store only the initiation date, and the recalls DDL, which had no PMA numbers.
    ddl = data_contract.CREATE_TABLE_DEVICE_RECALL_LINKS
    assert "PRIMARY KEY (pathway, application_number, res_event_number)" in ddl
    assert "pma_numbers VARCHAR[]" in data_contract.CREATE_TABLE_RECALLS
    assert "product_res_numbers VARCHAR[] NOT NULL" in ddl
    assert "availability_date DATE" in ddl
    assert "recall_status" not in ddl


def test_pma_numbers_are_normalized_to_their_base_p_number() -> None:
    # Sample: PMA numbers as openFDA writes them: a supplement suffix, a slash with a letter O for
    # zero, a "PMA-" prefix in lower case, and a repeat. Only the base number matches FDA's PMA export.
    rec = product("Z-1", ks=(), pma_numbers=["P850020S011", "P050052/SO52", "pma-p900023", " P850020 "])
    assert rec.pma_numbers == ["P850020", "P050052", "P900023"]


def test_pma_value_without_a_p_number_is_kept_as_written_and_gets_no_label() -> None:
    # Sample: a PMA field holding "H990014" (an HDE number) and "N/A". Neither is a PMA number,
    # so neither is dropped silently nor turned into a PMA label.
    rec = product("Z-1", ks=(), pma_numbers=["H990014", "n/a"])
    assert rec.pma_numbers == ["H990014", "N/A"]
    assert r.device_recall_events([rec]) == []


def test_device_recall_events_give_a_pma_recall_a_pma_label() -> None:
    # Sample: two products of event EV7 naming PMA P910020, one through a supplement number.
    [label] = r.device_recall_events(
        [product("Z-1", "EV7", (), pma_numbers=["P910020"]), product("Z-2", "EV7", (), pma_numbers=["P910020S021"])]
    )
    assert (label.pathway, label.application_number, label.product_res_numbers) == ("pma", "P910020", ("Z-1", "Z-2"))


def test_product_naming_a_k_number_and_a_pma_gets_a_label_on_each_pathway() -> None:
    # Sample: one product naming K180999 and P910020 (224 real no-K recalls on 510(k) product
    # codes carry PMA numbers; some products name both).
    labels = r.device_recall_events([product("Z-1", pma_numbers=["P910020"])])
    assert [(x.pathway, x.application_number) for x in labels] == [("510k", "K180999"), ("pma", "P910020")]


def test_from_openfda_reads_pma_numbers() -> None:
    # Sample: a minimal device/recall record for a PMA device, with no k_numbers key at all.
    rec = r.RecallRecord.from_openfda({"product_res_number": "Z-1", "res_event_number": "EV1", "pma_numbers": ["P910020"]})
    assert (rec.k_numbers, rec.pma_numbers) == ([], ["P910020"])
