"""Diffs one week's openFDA export against the previous week's, keyed per record (#40, #76, #91)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from filiation.core.schemas.run_manifest import KeyChange
from filiation.pipeline.crawler.openfda_bulk import ExportIntegrityError, iter_export_records

# Present on every record and never repeated: 510k/recall verified 2026-11-13 against the openFDA
# API, enforcement on the full 2026-11-16 export (#129).
RECORD_KEYS = {"510k": "k_number", "recall": "product_res_number", "enforcement": "recall_number"}
# Keys records whose RECORD_KEYS field is blank; the 2026-11-16 enforcement export has one such record.
FALLBACK_KEYS = {"enforcement": "event_id"}


@dataclass(frozen=True)
class RecordDiff:
    """Counts of records added, removed, and amended, and how often each field changed."""

    added: int
    removed: int
    amended: int
    changed_fields: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_changes(cls, changes: Iterable[KeyChange]) -> RecordDiff:
        """Counts a per-key change list, so the counts and the stored list can never disagree.

        Args:
            changes: One change per key.

        Returns:
            RecordDiff: Added, removed, and amended counts, and changed fields in name order.
        """
        kinds: Counter[str] = Counter()
        fields: Counter[str] = Counter()
        for change in changes:
            kinds[change.change] += 1
            fields.update(change.fields)
        return cls(kinds["added"], kinds["removed"], kinds["amended"], dict(sorted(fields.items())))


def _digest(value: Any) -> bytes:
    """Hashes a JSON value so that key order inside objects does not matter.

    Args:
        value: Any JSON-decoded value.

    Returns:
        bytes: 16-byte BLAKE2b digest of its canonical JSON.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).digest()


def _cosmetic_form(value: Any) -> Any:
    """Rewrites a JSON value so that whitespace inside text and the order of list entries no longer matter.

    Args:
        value: Any JSON-decoded value.

    Returns:
        Any: The value with each string's whitespace runs collapsed to one space and trimmed,
            and each list sorted by its entries' canonical JSON, at every depth.
    """
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, dict):
        return {name: _cosmetic_form(item) for name, item in value.items()}
    if isinstance(value, list):
        items = [_cosmetic_form(item) for item in value]
        if all(isinstance(item, str) for item in items):
            return sorted(items)
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    return value


def _keyed_records(
    key_field: str, paths: list[Path], fallback_field: str | None = None
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Streams (key, record) pairs from every partition of one export.

    Args:
        key_field: Field that identifies a record, e.g. "k_number".
        paths: The export's partition zips.
        fallback_field: Field that keys a record whose key_field is blank, as
            "<fallback_field>=<value>"; None rejects such records.

    Yields:
        tuple[str, dict[str, Any]]: Each record with its key.

    Raises:
        ExportIntegrityError: If a record is not an object or its key is missing, empty, or
            not a string.
    """
    for path in paths:
        for record in iter_export_records(path):
            key = record.get(key_field) if isinstance(record, dict) else None
            if key == "" and fallback_field:
                fallback = record.get(fallback_field)
                key = f"{fallback_field}={fallback}" if isinstance(fallback, str) and fallback.strip() else None
                if key is None:
                    raise ExportIntegrityError(
                        f"{path.name}: record with a blank {key_field!r} and no usable {fallback_field!r}"
                    )
            if not isinstance(key, str) or not key:
                raise ExportIntegrityError(f"{path.name}: record without a usable {key_field!r}: {key!r}")
            yield key, record


def _record_digests(key_field: str, paths: list[Path], fallback_field: str | None) -> dict[str, bytes]:
    """Maps each record's key to the digest of its whole content.

    Args:
        key_field: Field that identifies a record.
        paths: The export's partition zips.
        fallback_field: Field that keys a record whose key_field is blank, or None.

    Returns:
        dict[str, bytes]: One digest per key.

    Raises:
        ExportIntegrityError: If a key appears twice or a record can't be keyed.
    """
    digests: dict[str, bytes] = {}
    for key, record in _keyed_records(key_field, paths, fallback_field):
        if key in digests:
            raise ExportIntegrityError(f"{key_field} {key!r} appears more than once in the export")
        digests[key] = _digest(record)
    return digests


def _field_digests(
    key_field: str, paths: list[Path], keys: set[str], fallback_field: str | None
) -> dict[str, dict[str, tuple[bytes, bytes]]]:
    """Maps each wanted key to per-field digests of its record, exact and in cosmetic form.

    Args:
        key_field: Field that identifies a record.
        paths: The export's partition zips.
        keys: Keys whose records to keep; all others are skipped.
        fallback_field: Field that keys a record whose key_field is blank, or None.

    Returns:
        dict[str, dict[str, tuple[bytes, bytes]]]: Field name to (exact digest, cosmetic-form
            digest), per wanted key.
    """
    return {
        key: {name: (_digest(value), _digest(_cosmetic_form(value))) for name, value in record.items()}
        for key, record in _keyed_records(key_field, paths, fallback_field)
        if key in keys
    }


def diff_export_changes(
    key_field: str, previous: list[Path], current: list[Path], fallback_field: str | None = None
) -> list[KeyChange]:
    """Lists, per record key, how two exports of one source differ.

    Records are matched on key_field. A record is amended when its content differs in any
    way other than key order; its changed top-level fields, including fields present on only
    one side, are listed, and those that differ only in whitespace or list order are also
    marked cosmetic (ADR-021). Each export is streamed twice: once to digest every record, then
    again to read field digests for the amended records only.

    Args:
        key_field: Field that identifies a record, from RECORD_KEYS.
        previous: Partition zips of the previous run's export.
        current: Partition zips of this run's export.
        fallback_field: Field from FALLBACK_KEYS that keys records whose key_field is blank;
            None rejects them.

    Returns:
        list[KeyChange]: One change per added, removed, or amended key, in key order, with an
            amendment's cosmetic fields marked.

    Raises:
        ExportIntegrityError: If either export has a duplicate key, an unkeyable record, or
            malformed JSON.
    """
    before = _record_digests(key_field, previous, fallback_field)
    after = _record_digests(key_field, current, fallback_field)
    amended = {key for key in before.keys() & after.keys() if before[key] != after[key]}

    changes = [KeyChange(key, "added") for key in after.keys() - before.keys()]
    changes += [KeyChange(key, "removed") for key in before.keys() - after.keys()]
    if amended:
        old_fields = _field_digests(key_field, previous, amended, fallback_field)
        new_fields = _field_digests(key_field, current, amended, fallback_field)
        for key in amended:
            old, new = old_fields[key], new_fields[key]
            fields = tuple(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))
            cosmetic = tuple(name for name in fields if name in old and name in new and old[name][1] == new[name][1])
            changes.append(KeyChange(key, "amended", fields, cosmetic))
    return sorted(changes, key=lambda change: change.key)


def diff_exports(
    key_field: str, previous: list[Path], current: list[Path], fallback_field: str | None = None
) -> RecordDiff:
    """Counts how two exports of one source differ, from their per-key change list.

    Args:
        key_field: Field that identifies a record, from RECORD_KEYS.
        previous: Partition zips of the previous run's export.
        current: Partition zips of this run's export.
        fallback_field: Field that keys records whose key_field is blank, or None.

    Returns:
        RecordDiff: Added, removed, and amended counts, and changed fields in name order.

    Raises:
        ExportIntegrityError: If either export has a duplicate key, an unkeyable record, or
            malformed JSON.
    """
    return RecordDiff.from_changes(diff_export_changes(key_field, previous, current, fallback_field))
