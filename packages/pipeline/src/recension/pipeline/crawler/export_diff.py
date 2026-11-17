"""Diffs one week's openFDA export against the previous week's, keyed per record (#40)."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recension.pipeline.crawler.openfda_bulk import ExportIntegrityError, iter_export_records

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


def _digest(value: Any) -> bytes:
    """Hashes a JSON value so that key order inside objects does not matter.

    Args:
        value: Any JSON-decoded value.

    Returns:
        bytes: 16-byte BLAKE2b digest of its canonical JSON.
    """
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.blake2b(canonical.encode("utf-8"), digest_size=16).digest()


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
) -> dict[str, dict[str, bytes]]:
    """Maps each wanted key to per-field digests of its record.

    Args:
        key_field: Field that identifies a record.
        paths: The export's partition zips.
        keys: Keys whose records to keep; all others are skipped.
        fallback_field: Field that keys a record whose key_field is blank, or None.

    Returns:
        dict[str, dict[str, bytes]]: Field name to value digest, per wanted key.
    """
    return {
        key: {name: _digest(value) for name, value in record.items()}
        for key, record in _keyed_records(key_field, paths, fallback_field)
        if key in keys
    }


def diff_exports(
    key_field: str, previous: list[Path], current: list[Path], fallback_field: str | None = None
) -> RecordDiff:
    """Compares two exports of one source record by record.

    Records are matched on key_field. A record is amended when its content differs in any
    way other than key order; the top-level fields that differ, including fields present on
    only one side, are counted. Each export is streamed twice: once to digest every record,
    then again to read field digests for the amended records only.

    Args:
        key_field: Field that identifies a record, from RECORD_KEYS.
        previous: Partition zips of the previous run's export.
        current: Partition zips of this run's export.
        fallback_field: Field from FALLBACK_KEYS that keys records whose key_field is blank;
            None rejects them.

    Returns:
        RecordDiff: Added, removed, and amended counts, and changed fields in name order.

    Raises:
        ExportIntegrityError: If either export has a duplicate key, an unkeyable record, or
            malformed JSON.
    """
    before = _record_digests(key_field, previous, fallback_field)
    after = _record_digests(key_field, current, fallback_field)
    amended = {key for key in before.keys() & after.keys() if before[key] != after[key]}

    changed: Counter[str] = Counter()
    if amended:
        old_fields = _field_digests(key_field, previous, amended, fallback_field)
        new_fields = _field_digests(key_field, current, amended, fallback_field)
        for key in amended:
            old, new = old_fields[key], new_fields[key]
            changed.update(name for name in old.keys() | new.keys() if old.get(name) != new.get(name))

    return RecordDiff(
        added=len(after.keys() - before.keys()),
        removed=len(before.keys() - after.keys()),
        amended=len(amended),
        changed_fields=dict(sorted(changed.items())),
    )
