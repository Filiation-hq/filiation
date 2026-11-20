"""Checks that every record of an openFDA export can be keyed once and that its key fields keep their types (#169)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from filiation.pipeline.crawler.findings import CheckSpec, Finding, register

# Present on every record and never repeated: 510k/recall verified 2026-11-13 against the openFDA
# API, enforcement on the full 2026-11-16 export (#129); all three again on the 2026-11-18/20/19 exports.
RECORD_KEYS = {"510k": "k_number", "recall": "product_res_number", "enforcement": "recall_number"}
# Keys records whose RECORD_KEYS field is blank; the 2026-11-19 enforcement export has one such record.
FALLBACK_KEYS = {"enforcement": "event_id"}

# Top-level fields of the 2026-11-18 510(k), 2026-11-20 recall and 2026-11-19 enforcement exports.
KNOWN_FIELDS = {
    "510k": frozenset(
        "address_1 address_2 advisory_committee advisory_committee_description applicant city clearance_type "
        "contact country_code date_received decision_code decision_date decision_description device_name "
        "expedited_review_flag k_number openfda postal_code product_code review_advisory_committee state "
        "statement_or_summary third_party_flag zip_code".split()
    ),
    "recall": frozenset(
        "action additional_info_contact address_1 address_2 cfres_id city code_info country distribution_pattern "
        "event_date_created event_date_initiated event_date_posted event_date_terminated firm_fei_number k_numbers "
        "openfda pma_numbers postal_code product_code product_description product_quantity product_res_number "
        "reason_for_recall recall_status recalling_firm res_event_number root_cause_description state".split()
    ),
    "enforcement": frozenset(
        "address_1 address_2 center_classification_date city classification code_info country distribution_pattern "
        "event_id initial_firm_notification more_code_info openfda postal_code product_description product_quantity "
        "product_type reason_for_recall recall_initiation_date recall_number recalling_firm report_date state status "
        "termination_date voluntary_mandated".split()
    ),
}

# Identity, date and linkage fields downstream code reads, with the one type each had on every
# record of those exports. The key field itself is checked by key_missing instead.
FIELD_TYPES = {
    "510k": {
        "date_received": "str",
        "decision_date": "str",
        "product_code": "str",
        "statement_or_summary": "str",
    },
    "recall": {
        "res_event_number": "str",
        "cfres_id": "str",
        "product_code": "str",
        "k_numbers": "list[str]",
        "pma_numbers": "list[str]",
        "event_date_initiated": "str",
        "event_date_posted": "str",
        "event_date_created": "str",
        "event_date_terminated": "str",
    },
    "enforcement": {
        "event_id": "str",
        "classification": "str",
        "product_type": "str",
        "recall_initiation_date": "str",
        "report_date": "str",
        "center_classification_date": "str",
        "termination_date": "str",
    },
}

KEY_MISSING = register(CheckSpec("identity.key_missing", "fail", ("R14",)))
DUPLICATE_EXACT = register(CheckSpec("identity.duplicate_exact", "fail", ("R18",)))
DUPLICATE_CONFLICTING = register(CheckSpec("identity.duplicate_conflicting", "fail", ("R18",)))
FIELD_TYPE = register(CheckSpec("identity.field_type", "fail", ("R14",)))
FALLBACK_KEY = register(CheckSpec("identity.fallback_key", "report", ("R14",)))
UNEXPECTED_FIELD = register(CheckSpec("identity.unexpected_field", "report", ("R14",)))
IDENTITY_CHECKS = (KEY_MISSING, DUPLICATE_EXACT, DUPLICATE_CONFLICTING, FIELD_TYPE, FALLBACK_KEY, UNEXPECTED_FIELD)

MAX_EXAMPLES = 5

Location = tuple[str, int]


def record_key(record: Any, key_field: str, fallback_field: str | None) -> tuple[str | None, bool]:
    """Finds the key that identifies one record, falling back to fallback_field when the key is blank.

    Args:
        record: One element of an export's "results" array.
        key_field: Field that identifies a record, e.g. "k_number".
        fallback_field: Field that keys a record whose key_field is blank, as
            "<fallback_field>=<value>"; None means such a record has no key.

    Returns:
        tuple[str | None, bool]: The key (None if the record can't be keyed), and whether
            the fallback supplied it.
    """
    if not isinstance(record, dict):
        return None, False
    key = record.get(key_field)
    if not isinstance(key, str):
        return None, False
    if key.strip():
        return key, False
    fallback = record.get(fallback_field) if fallback_field else None
    if isinstance(fallback, str) and fallback.strip():
        return f"{fallback_field}={fallback}", True
    return None, False


def _type_name(value: Any) -> str:
    """Names a JSON value's type, with the entry types for a list.

    Args:
        value: Decoded JSON value.

    Returns:
        str: e.g. "str", "int", "list[str]", or "list[int|str]".
    """
    if isinstance(value, list):
        inner = sorted({type(item).__name__ for item in value}) or ["str"]
        return f"list[{'|'.join(inner)}]"
    return type(value).__name__


def _digest(record: Any) -> bytes:
    """Hashes a record so that key order inside objects does not matter.

    Args:
        record: Decoded JSON record.

    Returns:
        bytes: 16-byte BLAKE2b digest of its canonical JSON.
    """
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).digest()


def _locations(places: list[Location]) -> str:
    """Formats where a key's records sit, grouping rows by partition.

    Args:
        places: (partition, row) pairs in observation order.

    Returns:
        str: e.g. "p1.json.zip rows 1, 2" or "part-1.json.zip row 0, part-2.json.zip row 1".
    """
    by_partition: dict[str, list[int]] = {}
    for partition, row in places:
        by_partition.setdefault(partition, []).append(row)
    return ", ".join(
        f"{partition} {'rows' if len(rows) > 1 else 'row'} {', '.join(map(str, rows))}"
        for partition, rows in by_partition.items()
    )


def _plural(count: int, noun: str) -> str:
    """Formats a count with its noun.

    Args:
        count: How many.
        noun: Singular noun, pluralized with "s".

    Returns:
        str: e.g. "1 key" or "1,000 records".
    """
    return f"{count:,} {noun}" + ("" if count == 1 else "s")


def _shown(examples: list[str], total: int) -> str:
    """Joins up to MAX_EXAMPLES examples, with an ellipsis when more exist.

    Args:
        examples: Examples already sorted.
        total: How many there are in all.

    Returns:
        str: The joined examples.
    """
    return "; ".join(examples[:MAX_EXAMPLES]) + (", …" if total > MAX_EXAMPLES else "")


class IdentityProfile:
    """Accumulates one source's keys, duplicates, and field types across all its partitions."""

    def __init__(self, source: str) -> None:
        """Starts an empty profile.

        Args:
            source: openFDA source name; selects the key, fallback, known fields, and field types.

        Raises:
            ValueError: If source has no declared key field.
        """
        if source not in RECORD_KEYS:
            raise ValueError(f"No record key declared for openFDA source {source!r}; expected one of {sorted(RECORD_KEYS)}")
        self.source = source
        self.key_field = RECORD_KEYS[source]
        self.fallback_field = FALLBACK_KEYS.get(source)
        self._rows = 0
        self._first: dict[str, tuple[bytes, Location]] = {}
        self._repeats: dict[str, list[Location]] = {}
        self._conflicting: set[str] = set()
        self._unkeyable: list[Location] = []
        self._fallbacks: dict[str, Location] = {}
        self._mistyped: dict[str, list[tuple[str, Location]]] = {}
        self._unexpected: dict[str, int] = {}

    @property
    def checks(self) -> list[str]:
        """Lists the ids of every identity check, all of which run on every source.

        Returns:
            list[str]: Check ids in order.
        """
        return sorted(spec.check_id for spec in IDENTITY_CHECKS)

    def observe(self, record: Any, partition: str, row: int) -> None:
        """Adds one record's key and field types to the profile.

        Args:
            record: One element of the export's "results" array.
            partition: File name of the partition it came from.
            row: Its 0-based index within that partition.
        """
        self._rows += 1
        where = (partition, row)
        key, fell_back = record_key(record, self.key_field, self.fallback_field)
        if key is None:
            self._unkeyable.append(where)
            return
        if fell_back:
            self._fallbacks[key] = where
        digest = _digest(record)
        if key in self._first:
            first_digest, first_where = self._first[key]
            self._repeats.setdefault(key, [first_where]).append(where)
            if digest != first_digest:
                self._conflicting.add(key)
        else:
            self._first[key] = (digest, where)
        self._check_fields(record, key, where)

    def _check_fields(self, record: dict[str, Any], key: str, where: Location) -> None:
        """Records declared fields of the wrong type and fields no measured export had.

        Args:
            record: The record, already known to be an object.
            key: Its key.
            where: Its (partition, row).
        """
        for name, expected in FIELD_TYPES[self.source].items():
            value = record.get(name)
            if value is not None and (found := _type_name(value)) != expected:
                self._mistyped.setdefault(name, []).append((f"{key} {name} is {found}, expected {expected}", where))
        for name in record.keys() - KNOWN_FIELDS[self.source]:
            self._unexpected[name] = self._unexpected.get(name, 0) + 1

    def findings(self) -> list[Finding]:
        """Reports everything the profile found, one finding per check, in check-id order.

        Returns:
            list[Finding]: Fail findings for unusable keys, duplicates, and type changes;
                report findings for fallback keys and unexpected fields; empty when clean.
        """
        found = []
        if self._unkeyable:
            places = sorted(self._unkeyable)
            examples = [_locations([place]) for place in places]
            message = f"{_plural(len(places), 'record')} with no usable {self.key_field!r}"
            found.append(KEY_MISSING.finding(self.source, f"{message}: {_shown(examples, len(places))}"))
        keyed = self._rows - len(self._unkeyable)
        distinct = f"{_plural(keyed, 'row')}, {_plural(len(self._first), 'distinct key')}"
        for spec, keys, text in (
            (DUPLICATE_CONFLICTING, sorted(self._conflicting), "shared by records with different content"),
            (DUPLICATE_EXACT, sorted(self._repeats.keys() - self._conflicting), "repeated by identical records"),
        ):
            if keys:
                examples = [f"{key} ({_locations(self._repeats[key])})" for key in keys]
                found.append(spec.finding(self.source, f"{_plural(len(keys), 'key')} {text} ({distinct}): {_shown(examples, len(keys))}"))
        if self._mistyped:
            hits = sorted(hit for hits in self._mistyped.values() for hit in hits)
            fields = ", ".join(f"{name} {len(hits):,}" for name, hits in sorted(self._mistyped.items()))
            examples = [f"{text} ({_locations([where])})" for text, where in hits]
            message = f"{_plural(len(hits), 'field value')} of the wrong type ({fields})"
            found.append(FIELD_TYPE.finding(self.source, f"{message}: {_shown(examples, len(hits))}"))
        if self._fallbacks:
            keys = sorted(self._fallbacks)
            examples = [f"{key} ({_locations([self._fallbacks[key]])})" for key in keys]
            message = f"{_plural(len(keys), 'record')} with a blank {self.key_field!r} keyed by {self.fallback_field!r}"
            found.append(FALLBACK_KEY.finding(self.source, f"{message}: {_shown(examples, len(keys))}"))
        if self._unexpected:
            names = sorted(self._unexpected)
            examples = [f"{name} ({_plural(self._unexpected[name], 'record')})" for name in names]
            message = f"{_plural(len(names), 'field')} not in the 2026-11 exports"
            found.append(UNEXPECTED_FIELD.finding(self.source, f"{message}: {_shown(examples, len(names))}"))
        return sorted(found, key=lambda f: f.check.check_id)
