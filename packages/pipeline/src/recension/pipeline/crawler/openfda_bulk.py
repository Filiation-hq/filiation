"""Ingests openFDA bulk-export zips into the write-once raw store (ADR-010, #39)."""

from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import IO, Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from recension.core.storage.raw_store import RawStore

USER_AGENT = "Recension/0.1.0 (device-lineage-research)"

DOWNLOAD_INDEX_URL = "https://api.fda.gov/download.json"
SOURCES = {"510k": ("device", "510k"), "recall": ("device", "recall")}
CHUNK_CHARS = 1 << 20

Opener = Callable[[str], IO[bytes]]


class ExportIntegrityError(Exception):
    """Raised when a downloaded export does not match what the download index promises."""


class ExportConflictError(Exception):
    """Raised when an export_date already stored was re-published with different bytes."""


@dataclass(frozen=True)
class ExportPartition:
    """One partition file listed in the download index."""

    url: str
    records: int
    file_name: str


@dataclass(frozen=True)
class ExportListing:
    """The download index entry for one source: its export_date and partitions."""

    source: str
    export_date: str
    partitions: list[ExportPartition]


@dataclass(frozen=True)
class PartitionResult:
    """What one partition contributed to a run, for the run manifest (#42)."""

    key: str
    sha256: str
    size_bytes: int
    records: int
    status: str
    last_updated: str | None = None


@dataclass(frozen=True)
class ExportStats:
    """What a streamed export reports about itself: its record count and meta.last_updated."""

    records: int
    last_updated: str | None


@dataclass(frozen=True)
class ExportResult:
    """Outcome of ingesting one source's export."""

    source: str
    export_date: str
    partitions: list[PartitionResult] = field(default_factory=list)


def default_opener(url: str) -> IO[bytes]:
    """Opens a URL over HTTPS with Recension's User-Agent.

    Args:
        url: URL to fetch.

    Returns:
        IO[bytes]: Response stream; the caller closes it.
    """
    request = Request(url, headers={"User-Agent": USER_AGENT})
    return urlopen(request, timeout=60)


def parse_export_listing(index: dict[str, Any], source: str) -> ExportListing:
    """Extracts one source's export_date and partitions from the parsed download index.

    Args:
        index: Parsed download.json.
        source: "510k" or "recall".

    Returns:
        ExportListing: The export_date and its partitions.

    Raises:
        ValueError: If source is unsupported or missing from the index, export_date is not
            YYYY-MM-DD, or the source lists no partitions or a partition with 0 records.
    """
    if source not in SOURCES:
        raise ValueError(f"Unsupported openFDA source {source!r}; expected one of {sorted(SOURCES)}")
    group, name = SOURCES[source]
    try:
        entry = index["results"][group][name]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"download.json has no entry for {group}/{name} (source {source!r})") from exc
    export_date = entry["export_date"]
    try:
        date.fromisoformat(export_date)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"export_date {export_date!r} is not YYYY-MM-DD") from exc
    partitions = [
        ExportPartition(
            url=p["file"],
            records=int(p["records"]),
            file_name=PurePosixPath(urlparse(p["file"]).path).name,
        )
        for p in entry["partitions"]
    ]
    if not partitions:
        raise ValueError(f"download.json lists no partitions for {source!r}")
    if empty := [p.file_name for p in partitions if p.records <= 0]:
        raise ValueError(f"download.json lists 0 records for {source!r} partitions {empty}")
    return ExportListing(source=source, export_date=export_date, partitions=partitions)


class _JsonStream:
    """Reads a large JSON text incrementally, decoding one value at a time."""

    def __init__(self, reader: IO[str]) -> None:
        """Wraps a text stream.

        Args:
            reader: Text stream positioned at the start of the JSON document.
        """
        self.reader = reader
        self.buffer = ""
        self.pos = 0
        self.eof = False
        self.decoder = json.JSONDecoder()

    def _fill(self) -> bool:
        """Appends the next chunk to the buffer, dropping text already consumed.

        Returns:
            bool: False once the stream is exhausted.
        """
        chunk = self.reader.read(CHUNK_CHARS)
        if not chunk:
            self.eof = True
            return False
        self.buffer = self.buffer[self.pos :] + chunk
        self.pos = 0
        return True

    def peek(self) -> str:
        """Skips whitespace and returns the next character without consuming it.

        Returns:
            str: The next non-whitespace character.

        Raises:
            ExportIntegrityError: If the document ends early.
        """
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if not self._fill():
                raise ExportIntegrityError("Export JSON ended unexpectedly")

    def expect(self, char: str) -> None:
        """Consumes the next non-whitespace character, which must be char.

        Args:
            char: The required character.

        Raises:
            ExportIntegrityError: If a different character comes next.
        """
        found = self.peek()
        if found != char:
            raise ExportIntegrityError(f"Expected {char!r} in export JSON, found {found!r}")
        self.pos += 1

    def value(self) -> Any:
        """Decodes the next complete JSON value, reading more text until it is whole.

        Returns:
            Any: The decoded value.

        Raises:
            ExportIntegrityError: If the value is malformed or the document ends early.
        """
        self.peek()
        while True:
            try:
                value, self.pos = self.decoder.raw_decode(self.buffer, self.pos)
                return value
            except json.JSONDecodeError as exc:
                if self.eof or not self._fill():
                    raise ExportIntegrityError(f"Malformed export JSON: {exc}") from exc


def _walk_export(stream: _JsonStream) -> Iterator[tuple[str, Any]]:
    """Yields ("meta", value) and ("record", value) events from an openFDA export document.

    Args:
        stream: Stream positioned at the start of an openFDA export document.

    Yields:
        tuple[str, Any]: The top-level meta object, and each record in "results" in order.

    Raises:
        ExportIntegrityError: If the document is malformed or has no top-level "results".
    """
    seen_results = False
    stream.expect("{")
    while stream.peek() != "}":
        key = stream.value()
        stream.expect(":")
        if key == "results" and stream.peek() == "[":
            seen_results = True
            stream.expect("[")
            while stream.peek() != "]":
                yield "record", stream.value()
                if stream.peek() == ",":
                    stream.expect(",")
            stream.expect("]")
        elif key == "meta":
            yield "meta", stream.value()
        else:
            stream.value()
        if stream.peek() == ",":
            stream.expect(",")
    if not seen_results:
        raise ExportIntegrityError("Export JSON has no top-level 'results' array")


@contextmanager
def _open_export(zip_path: Path) -> Iterator[_JsonStream]:
    """Opens the single JSON file inside an openFDA export zip as a stream.

    Args:
        zip_path: Path to a partition zip holding exactly one JSON file.

    Yields:
        _JsonStream: Stream positioned at the start of the export document.

    Raises:
        ExportIntegrityError: If the file is not a valid zip or holds more than one entry.
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise ExportIntegrityError(f"Expected one file in {zip_path.name}, found {names}")
            with archive.open(names[0]) as raw:
                yield _JsonStream(io.TextIOWrapper(raw, encoding="utf-8"))
    except zipfile.BadZipFile as exc:
        raise ExportIntegrityError(f"{zip_path.name} is not a valid zip (truncated download?)") from exc


def read_export_stats(zip_path: Path) -> ExportStats:
    """Streams an openFDA export zip to count its records and read meta.last_updated.

    Args:
        zip_path: Path to a partition zip holding exactly one JSON file.

    Returns:
        ExportStats: Record count and meta.last_updated.

    Raises:
        ExportIntegrityError: If the zip does not hold exactly one entry or its JSON is malformed.
    """
    count = 0
    last_updated = None
    with _open_export(zip_path) as stream:
        for kind, value in _walk_export(stream):
            if kind == "record":
                count += 1
            elif isinstance(value, dict):
                last_updated = value.get("last_updated")
    return ExportStats(count, last_updated)


def iter_export_records(zip_path: Path) -> Iterator[Any]:
    """Streams the records of an openFDA export zip one at a time, in file order.

    Args:
        zip_path: Path to a partition zip holding exactly one JSON file.

    Yields:
        Any: Each element of the file's "results" array, usually a dict.

    Raises:
        ExportIntegrityError: If the zip does not hold exactly one entry or its JSON is malformed.
    """
    with _open_export(zip_path) as stream:
        for kind, value in _walk_export(stream):
            if kind == "record":
                yield value


def count_export_records(zip_path: Path) -> int:
    """Counts the records in an openFDA export zip without loading the whole file.

    Args:
        zip_path: Path to a partition zip holding exactly one JSON file.

    Returns:
        int: Number of records in that file's "results" array.

    Raises:
        ExportIntegrityError: If the zip does not hold exactly one entry or its JSON is malformed.
    """
    return read_export_stats(zip_path).records


def _download(url: str, dest: Path, opener: Opener) -> tuple[str, int]:
    """Streams a URL to a file, hashing it on the way.

    Args:
        url: URL to download.
        dest: Local file to write.
        opener: Callable returning a byte stream for a URL.

    Returns:
        tuple[str, int]: Hex SHA-256 and size in bytes.
    """
    hasher = hashlib.sha256()
    size = 0
    with opener(url) as response, dest.open("wb") as out:
        while chunk := response.read(1 << 20):
            hasher.update(chunk)
            out.write(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _sha256_file(path: Path) -> str:
    """Hashes a local file.

    Args:
        path: File to hash.

    Returns:
        str: Hex SHA-256.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def ingest_export(
    source: str, store: RawStore, opener: Opener = default_opener, work_dir: Path | None = None
) -> ExportResult:
    """Downloads one source's current openFDA export, verifies it, and stores it write-once.

    Every partition is downloaded and its record count checked before anything is written.

    Args:
        source: "510k" or "recall".
        store: Raw store to write into.
        opener: Callable returning a byte stream for a URL; defaults to HTTPS.
        work_dir: Directory for temporary downloads; defaults to the system temp dir.

    Returns:
        ExportResult: Key, SHA-256, size, record count, and status ("stored" or
            "unchanged") for each partition.

    Raises:
        ValueError: If source is unsupported or the index is malformed.
        ExportIntegrityError: If a partition's record count differs from the index.
        ExportConflictError: If an already-stored export_date now has different bytes.
    """
    with opener(DOWNLOAD_INDEX_URL) as response:
        listing = parse_export_listing(json.load(response), source)

    results: list[PartitionResult] = []
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        downloads = []
        for partition in listing.partitions:
            path = Path(tmp) / partition.file_name
            sha256, size = _download(partition.url, path, opener)
            stats = read_export_stats(path)
            records = stats.records
            if records != partition.records:
                raise ExportIntegrityError(
                    f"{partition.file_name}: index lists {partition.records} records, zip holds {records}"
                )
            downloads.append((partition, path, sha256, size, records, stats.last_updated))

        for partition, path, sha256, size, records, last_updated in downloads:
            key = f"openfda/{source}/export_date={listing.export_date}/{partition.file_name}"
            if store.exists(key):
                stored_sha = _sha256_file(store.get(key, Path(tmp) / f"stored-{partition.file_name}"))
                if stored_sha != sha256:
                    raise ExportConflictError(
                        f"{key} already holds different bytes ({stored_sha[:12]} vs {sha256[:12]})"
                    )
                status = "unchanged"
            else:
                store.put_once(key, path)
                status = "stored"
            results.append(PartitionResult(key, sha256, size, records, status, last_updated))

    return ExportResult(source=source, export_date=listing.export_date, partitions=results)
