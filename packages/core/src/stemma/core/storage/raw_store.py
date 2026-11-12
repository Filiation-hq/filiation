"""Write-once storage for raw snapshots, on local disk or Cloudflare R2 (ADR-010)."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from stemma.core.utils.paths import get_raw_dir

DEFAULT_BUCKET = "stemma-raw"


class KeyExistsError(Exception):
    """Raised when put_once targets a key that already holds an object."""


class InvalidKeyError(ValueError):
    """Raised when a key is empty, absolute, uses backslashes, or contains '..'."""


def validate_key(key: str) -> str:
    """Checks that a key is a relative POSIX path that stays inside the store.

    Args:
        key: Object key in the ADR-010 layout, e.g. "cdrh/pdfs/K123456/<sha256>.pdf".

    Returns:
        str: The key unchanged.

    Raises:
        InvalidKeyError: If the key is empty, absolute, contains a backslash, or has a ".." part.
    """
    if not key or "\\" in key or key.startswith("/") or ".." in PurePosixPath(key).parts:
        raise InvalidKeyError(f"Invalid raw store key: {key!r}")
    return key


def _read_payload(data: bytes | Path) -> bytes:
    """Returns the bytes to store, reading them from disk when given a path.

    Args:
        data: Raw bytes, or a local file whose contents are stored.

    Returns:
        bytes: The payload.
    """
    return data if isinstance(data, bytes) else Path(data).read_bytes()


class RawStore(ABC):
    """Write-once object store for raw snapshots; existing objects are never replaced."""

    @abstractmethod
    def put_once(self, key: str, data: bytes | Path) -> str:
        """Stores data under key unless the key already exists.

        Args:
            key: Object key in the ADR-010 layout.
            data: Bytes, or a local file path, to store.

        Returns:
            str: Hex SHA-256 of the stored bytes.

        Raises:
            KeyExistsError: If an object already exists at key.
            InvalidKeyError: If key is malformed.
        """

    @abstractmethod
    def get(self, key: str, dest: Path) -> Path:
        """Copies the object at key to a local file.

        Args:
            key: Object key to fetch.
            dest: Local file path to write; parent directories are created.

        Returns:
            Path: dest, now holding the object's bytes.

        Raises:
            FileNotFoundError: If no object exists at key.
        """

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Reports whether an object exists at key.

        Args:
            key: Object key to check.

        Returns:
            bool: True if the object exists.
        """

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]:
        """Lists the keys that start with prefix.

        Args:
            prefix: Key prefix such as "openfda/510k/"; empty lists everything.

        Returns:
            list[str]: Matching keys, sorted.
        """


class LocalRawStore(RawStore):
    """RawStore backed by a directory, by default data/raw/ in the repo."""

    def __init__(self, root: Path | None = None) -> None:
        """Creates a store rooted at a local directory.

        Args:
            root: Directory that holds the keys; defaults to get_raw_dir().
        """
        self.root = Path(root) if root is not None else get_raw_dir()

    def _path(self, key: str) -> Path:
        """Maps a validated key to its file under the root.

        Args:
            key: Object key.

        Returns:
            Path: <root>/<key>.
        """
        return self.root / validate_key(key)

    def put_once(self, key: str, data: bytes | Path) -> str:
        """Stores data under key unless the key already exists.

        Args:
            key: Object key in the ADR-010 layout.
            data: Bytes, or a local file path, to store.

        Returns:
            str: Hex SHA-256 of the stored bytes.

        Raises:
            KeyExistsError: If a file already exists at key.
            InvalidKeyError: If key is malformed.
        """
        target = self._path(key)
        payload = _read_payload(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as tmp:
                tmp.write(payload)
            # os.link fails atomically if the target exists, so a partial or racing
            # write can never replace a stored object.
            os.link(tmp_name, target)
        except FileExistsError as exc:
            raise KeyExistsError(key) from exc
        finally:
            os.unlink(tmp_name)
        return hashlib.sha256(payload).hexdigest()

    def get(self, key: str, dest: Path) -> Path:
        """Copies the file at key to a local path.

        Args:
            key: Object key to fetch.
            dest: Local file path to write; parent directories are created.

        Returns:
            Path: dest, now holding the object's bytes.

        Raises:
            FileNotFoundError: If no file exists at key.
        """
        source = self._path(key)
        if not source.is_file():
            raise FileNotFoundError(key)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return dest

    def exists(self, key: str) -> bool:
        """Reports whether a file exists at key.

        Args:
            key: Object key to check.

        Returns:
            bool: True if the file exists.
        """
        return self._path(key).is_file()

    def list(self, prefix: str = "") -> list[str]:
        """Lists the stored keys that start with prefix, skipping in-flight temp files.

        Args:
            prefix: Key prefix; empty lists everything.

        Returns:
            list[str]: Matching keys, sorted.
        """
        if not self.root.is_dir():
            return []
        keys = (
            p.relative_to(self.root).as_posix()
            for p in self.root.rglob("*")
            if p.is_file() and not p.name.startswith(".tmp-")
        )
        return sorted(k for k in keys if k.startswith(prefix))


def _error_status(exc: Exception) -> tuple[str, int]:
    """Extracts the error code and HTTP status from a botocore ClientError.

    Args:
        exc: A botocore.exceptions.ClientError.

    Returns:
        tuple[str, int]: (error code, HTTP status code).
    """
    response = getattr(exc, "response", {})
    code = response.get("Error", {}).get("Code", "")
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
    return code, status


class R2RawStore(RawStore):
    """RawStore backed by a Cloudflare R2 bucket through the S3 API (boto3)."""

    def __init__(self, bucket: str, client: Any) -> None:
        """Creates a store over an existing S3-compatible client.

        Args:
            bucket: Bucket name, normally "stemma-raw".
            client: boto3 S3 client configured with the R2 endpoint.
        """
        self.bucket = bucket
        self.client = client

    def put_once(self, key: str, data: bytes | Path) -> str:
        """Stores data under key with If-None-Match: *, so R2 refuses an existing key.

        Args:
            key: Object key in the ADR-010 layout.
            data: Bytes, or a local file path, to store.

        Returns:
            str: Hex SHA-256 of the stored bytes, also saved as object metadata.

        Raises:
            KeyExistsError: If R2 answers 412 PreconditionFailed.
            InvalidKeyError: If key is malformed.
            botocore.exceptions.ClientError: For any other S3 error.
        """
        from botocore.exceptions import ClientError

        payload = _read_payload(data)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=validate_key(key),
                Body=payload,
                IfNoneMatch="*",
                Metadata={"sha256": digest},
            )
        except ClientError as exc:
            code, status = _error_status(exc)
            if code == "PreconditionFailed" or status == 412:
                raise KeyExistsError(key) from exc
            raise
        return digest

    def get(self, key: str, dest: Path) -> Path:
        """Streams the object at key to a local file.

        Args:
            key: Object key to fetch.
            dest: Local file path to write; parent directories are created.

        Returns:
            Path: dest, now holding the object's bytes.

        Raises:
            FileNotFoundError: If R2 answers NoSuchKey or 404.
            botocore.exceptions.ClientError: For any other S3 error.
        """
        from botocore.exceptions import ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=validate_key(key))
        except ClientError as exc:
            code, status = _error_status(exc)
            if code in ("NoSuchKey", "404") or status == 404:
                raise FileNotFoundError(key) from exc
            raise
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            shutil.copyfileobj(response["Body"], out)
        return dest

    def exists(self, key: str) -> bool:
        """Reports whether an object exists at key using HeadObject.

        Args:
            key: Object key to check.

        Returns:
            bool: True if HeadObject succeeds, False on 404.

        Raises:
            botocore.exceptions.ClientError: For errors other than 404.
        """
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=validate_key(key))
        except ClientError as exc:
            code, status = _error_status(exc)
            if code in ("NoSuchKey", "404") or status == 404:
                return False
            raise
        return True

    def list(self, prefix: str = "") -> list[str]:
        """Lists every key under prefix, following ListObjectsV2 pagination.

        Args:
            prefix: Key prefix; empty lists the whole bucket.

        Returns:
            list[str]: Matching keys, sorted.
        """
        keys: list[str] = []
        params: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
        while True:
            page = self.client.list_objects_v2(**params)
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
            if not page.get("IsTruncated"):
                return sorted(keys)
            params["ContinuationToken"] = page["NextContinuationToken"]


def raw_store_from_env(
    environ: Mapping[str, str] | None = None, local_root: Path | None = None
) -> RawStore:
    """Builds the RawStore named by STEMMA_RAW_STORE ("local" by default, or "r2").

    Args:
        environ: Environment to read; defaults to os.environ. For "r2" it needs
            STEMMA_R2_ENDPOINT and optionally STEMMA_R2_BUCKET; credentials come from
            AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY as boto3 reads them.
        local_root: Root for the local store; defaults to data/raw/.

    Returns:
        RawStore: A LocalRawStore or an R2RawStore.

    Raises:
        ValueError: If the backend name is unknown or r2 has no endpoint.
        ImportError: If "r2" is selected without boto3 (install stemma-core[remote]).
    """
    env = os.environ if environ is None else environ
    backend = env.get("STEMMA_RAW_STORE", "local").lower()
    if backend == "local":
        return LocalRawStore(local_root)
    if backend != "r2":
        raise ValueError(f"Unknown STEMMA_RAW_STORE {backend!r}; expected 'local' or 'r2'")
    endpoint = env.get("STEMMA_R2_ENDPOINT")
    if not endpoint:
        raise ValueError("STEMMA_RAW_STORE=r2 requires STEMMA_R2_ENDPOINT")
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="auto",
        aws_access_key_id=env.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=env.get("AWS_SECRET_ACCESS_KEY"),
    )
    return R2RawStore(env.get("STEMMA_R2_BUCKET", DEFAULT_BUCKET), client)
