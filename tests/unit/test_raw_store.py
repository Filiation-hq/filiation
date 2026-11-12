"""Unit tests for the write-once raw snapshot store in stemma.core.storage.raw_store (ADR-010).

Task map:
    Input: a key in the ADR-010 layout (e.g. "openfda/510k/export_date=2026-11-09/x.json.zip")
        and the bytes or local file to store under it.
    Output: put_once stores the object and returns its SHA-256; get copies it to a local path;
        exists and list report what the store holds.
    Edge cases: writing a key that already exists (refused, never overwritten); keys that
        escape the store root ("..", absolute paths, backslashes, empty); a missing key on get
        and exists; list pagination on R2; an unknown or misconfigured backend name.
    Invariants: an existing object is never replaced; local and R2 behave the same; R2
        writes carry If-None-Match: * so the bucket enforces write-once too; no network.
"""

import hashlib
import io
from pathlib import Path

import pytest
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from stemma.core.storage.raw_store import (
    LATEST_KEY,
    InvalidKeyError,
    KeyExistsError,
    LocalRawStore,
    R2RawStore,
    raw_store_from_env,
)

BUCKET = "stemma-raw"
KEY = "openfda/510k/export_date=2026-11-09/device-510k-0001-of-0001.json.zip"
PAYLOAD = b'{"results": []}'
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def make_r2_client():
    """Builds an offline boto3 S3 client pointed at a fake R2 endpoint.

    Returns:
        botocore.client.S3: Client with dummy credentials; only usable under a Stubber.
    """
    import boto3

    return boto3.client(
        "s3",
        endpoint_url="https://account.r2.cloudflarestorage.com",
        region_name="auto",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


def put_params(body=ANY) -> dict:
    """Returns the put_object parameters R2RawStore must send for KEY.

    Args:
        body: Expected Body argument; ANY by default.

    Returns:
        dict: Expected params, including the If-None-Match write-once guard and the
            per-call write id that tells our own retried write apart from someone else's.
    """
    return {
        "Bucket": BUCKET,
        "Key": KEY,
        "Body": body,
        "IfNoneMatch": "*",
        "Metadata": {"sha256": PAYLOAD_SHA, "write-id": ANY},
    }


def local_store_written_twice(tmp_path: Path):
    """Returns a LocalRawStore and a no-op stub check for the shared write-once test.

    Args:
        tmp_path: Empty temporary directory used as the store root.

    Returns:
        tuple: (store, finish) where finish() asserts nothing for the local backend.
    """
    return LocalRawStore(tmp_path), lambda: None


def r2_store_written_twice(tmp_path: Path):
    """Returns an R2RawStore whose stub accepts one put and rejects the second with 412.

    Args:
        tmp_path: Unused; keeps the same signature as the local factory.

    Returns:
        tuple: (store, finish) where finish() asserts every stubbed call was made.
    """
    client = make_r2_client()
    stubber = Stubber(client)
    stubber.add_response("put_object", {"ETag": '"abc"'}, put_params())
    stubber.add_client_error(
        "put_object",
        service_error_code="PreconditionFailed",
        http_status_code=412,
        expected_params=put_params(),
    )
    stubber.add_response(
        "head_object",
        {"Metadata": {"sha256": PAYLOAD_SHA, "write-id": "an-earlier-run"}},
        {"Bucket": BUCKET, "Key": KEY},
    )
    stubber.activate()
    return R2RawStore(BUCKET, client), stubber.assert_no_pending_responses


@pytest.mark.parametrize(
    "make_store", [local_store_written_twice, r2_store_written_twice], ids=["local", "r2"]
)
def test_put_once_refuses_to_overwrite_an_existing_key_on_every_backend(tmp_path, make_store):
    # Sample: the 2026-11-09 510(k) export key written twice. The first write stores
    # PAYLOAD; the second (different bytes) must raise KeyExistsError. On R2 the stub
    # answers the second put with 412 PreconditionFailed, as R2 does for If-None-Match: *.
    store, finish = make_store(tmp_path)
    assert store.put_once(KEY, PAYLOAD) == PAYLOAD_SHA
    with pytest.raises(KeyExistsError):
        store.put_once(KEY, PAYLOAD)
    finish()


def test_local_put_once_keeps_original_bytes_after_refused_overwrite(tmp_path):
    # Sample: PAYLOAD written, then b"tampered" attempted under the same key.
    # The file on disk must still hold PAYLOAD.
    store = LocalRawStore(tmp_path)
    store.put_once(KEY, PAYLOAD)
    with pytest.raises(KeyExistsError):
        store.put_once(KEY, b"tampered")
    assert (tmp_path / KEY).read_bytes() == PAYLOAD


def test_local_put_once_accepts_a_source_file_and_returns_its_sha256(tmp_path):
    # Sample: a local file holding PAYLOAD (how the bulk-export zip arrives).
    # Expect the same bytes under the key and the SHA-256 of PAYLOAD.
    source = tmp_path / "download.zip"
    source.write_bytes(PAYLOAD)
    store = LocalRawStore(tmp_path / "raw")
    assert store.put_once(KEY, source) == PAYLOAD_SHA
    assert (tmp_path / "raw" / KEY).read_bytes() == PAYLOAD


def test_local_put_once_leaves_no_temporary_files_behind(tmp_path):
    # Sample: one successful write and one refused write of KEY.
    # Only the final object may remain in the export_date directory.
    store = LocalRawStore(tmp_path)
    store.put_once(KEY, PAYLOAD)
    with pytest.raises(KeyExistsError):
        store.put_once(KEY, PAYLOAD)
    assert [p.name for p in (tmp_path / KEY).parent.iterdir()] == [Path(KEY).name]


@pytest.mark.parametrize(
    "bad_key",
    ["", "../escape.json", "openfda/../../escape.json", "/etc/passwd", "cdrh\\pdfs\\K1.pdf"],
)
def test_local_store_rejects_keys_that_escape_or_break_the_layout(tmp_path, bad_key):
    # Sample: empty, parent-directory, absolute, and backslash keys. Each could write
    # outside data/raw/ or produce a key that differs between local and R2.
    with pytest.raises(InvalidKeyError):
        LocalRawStore(tmp_path).put_once(bad_key, PAYLOAD)


def test_local_get_copies_object_to_destination_path(tmp_path):
    # Sample: KEY stored with PAYLOAD; get into a separate cache directory.
    store = LocalRawStore(tmp_path / "raw")
    store.put_once(KEY, PAYLOAD)
    dest = store.get(KEY, tmp_path / "cache" / "x.zip")
    assert dest.read_bytes() == PAYLOAD


def test_local_get_raises_file_not_found_for_missing_key(tmp_path):
    # Sample: empty store; any key is missing.
    with pytest.raises(FileNotFoundError):
        LocalRawStore(tmp_path).get(KEY, tmp_path / "out.zip")


def test_local_exists_and_list_report_stored_keys_under_a_prefix(tmp_path):
    # Sample: one 510(k) export and one CDRH PDF. Listing "openfda/" returns only the export;
    # listing "" returns both in sorted order.
    store = LocalRawStore(tmp_path)
    pdf_key = f"cdrh/pdfs/K123456/{PAYLOAD_SHA}.pdf"
    store.put_once(KEY, PAYLOAD)
    store.put_once(pdf_key, PAYLOAD)
    assert store.exists(KEY) and not store.exists("openfda/recall/missing.zip")
    assert store.list("openfda/") == [KEY]
    assert store.list() == [pdf_key, KEY]


def test_r2_get_streams_object_body_to_destination_path(tmp_path):
    # Sample: stubbed get_object returning PAYLOAD as a streaming body.
    client = make_r2_client()
    with Stubber(client) as stubber:
        stubber.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(PAYLOAD), len(PAYLOAD))},
            {"Bucket": BUCKET, "Key": KEY},
        )
        dest = R2RawStore(BUCKET, client).get(KEY, tmp_path / "x.zip")
    assert dest.read_bytes() == PAYLOAD


def test_r2_get_raises_file_not_found_for_missing_key(tmp_path):
    # Sample: stubbed get_object failing with NoSuchKey (404), R2's answer for a missing key.
    client = make_r2_client()
    with Stubber(client) as stubber:
        stubber.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        with pytest.raises(FileNotFoundError):
            R2RawStore(BUCKET, client).get(KEY, tmp_path / "x.zip")


def test_r2_exists_is_true_on_head_hit_and_false_on_404():
    # Sample: one head_object success, then one 404 for a missing recall export key.
    client = make_r2_client()
    with Stubber(client) as stubber:
        stubber.add_response("head_object", {}, {"Bucket": BUCKET, "Key": KEY})
        stubber.add_client_error("head_object", "404", http_status_code=404)
        store = R2RawStore(BUCKET, client)
        assert store.exists(KEY) is True
        assert store.exists("openfda/recall/missing.zip") is False


def test_r2_list_follows_pagination_and_returns_sorted_keys():
    # Sample: two list_objects_v2 pages ("b" then "a") joined by a continuation token.
    # Expect every key from both pages, sorted.
    client = make_r2_client()
    with Stubber(client) as stubber:
        stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "openfda/b"}], "IsTruncated": True, "NextContinuationToken": "t"},
            {"Bucket": BUCKET, "Prefix": "openfda/"},
        )
        stubber.add_response(
            "list_objects_v2",
            {"Contents": [{"Key": "openfda/a"}], "IsTruncated": False},
            {"Bucket": BUCKET, "Prefix": "openfda/", "ContinuationToken": "t"},
        )
        assert R2RawStore(BUCKET, client).list("openfda/") == ["openfda/a", "openfda/b"]


def test_raw_store_from_env_defaults_to_local_store_under_given_root(tmp_path):
    # Sample: no STEMMA_RAW_STORE set. Local is the default so dev runs never touch R2.
    store = raw_store_from_env({}, local_root=tmp_path)
    assert isinstance(store, LocalRawStore) and store.root == tmp_path


def test_raw_store_from_env_builds_r2_store_from_endpoint_and_bucket(tmp_path):
    # Sample: STEMMA_RAW_STORE=r2 with an endpoint; bucket falls back to stemma-raw.
    env = {
        "STEMMA_RAW_STORE": "r2",
        "STEMMA_R2_ENDPOINT": "https://account.r2.cloudflarestorage.com",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
    }
    store = raw_store_from_env(env, local_root=tmp_path)
    assert isinstance(store, R2RawStore) and store.bucket == BUCKET


@pytest.mark.parametrize(
    "env",
    [{"STEMMA_RAW_STORE": "s3"}, {"STEMMA_RAW_STORE": "r2"}],
    ids=["unknown-backend", "r2-without-endpoint"],
)
def test_raw_store_from_env_rejects_unknown_or_incomplete_configuration(tmp_path, env):
    # Sample: an unsupported backend name, and r2 with no STEMMA_R2_ENDPOINT.
    # Both must fail loudly instead of silently writing somewhere else.
    with pytest.raises(ValueError):
        raw_store_from_env(env, local_root=tmp_path)


def test_local_write_latest_replaces_the_pointer_and_only_the_pointer(tmp_path):
    # Sample: latest.json written for run 101, then for run 102. latest.json is the one
    # mutable object (ADR-010), so the second write replaces the first.
    store = LocalRawStore(tmp_path)
    store.write_latest(b'{"run_id": "101"}')
    store.write_latest(b'{"run_id": "102"}')
    assert (tmp_path / LATEST_KEY).read_bytes() == b'{"run_id": "102"}'
    assert store.list() == [LATEST_KEY]


def test_r2_write_latest_puts_pointer_without_if_none_match():
    # Sample: stubbed put_object for latest.json. Unlike put_once, the pointer write must
    # not send IfNoneMatch, or every run after the first would fail with 412.
    client = make_r2_client()
    with Stubber(client) as stubber:
        stubber.add_response(
            "put_object",
            {"ETag": '"p"'},
            {"Bucket": BUCKET, "Key": LATEST_KEY, "Body": b'{"run_id": "102"}', "ContentType": "application/json"},
        )
        R2RawStore(BUCKET, client).write_latest(b'{"run_id": "102"}')


def r2_conflict_store(stored_write_id: str) -> tuple:
    """Builds an R2RawStore whose put gets 412 and whose stored object carries stored_write_id.

    Args:
        stored_write_id: write-id metadata on the object already at KEY.

    Returns:
        tuple: (store using write id "w-this-call", stubber).
    """
    client = make_r2_client()
    stubber = Stubber(client)
    stubber.add_client_error(
        "put_object", service_error_code="PreconditionFailed", http_status_code=412, expected_params=put_params()
    )
    stubber.add_response(
        "head_object",
        {"Metadata": {"sha256": PAYLOAD_SHA, "write-id": stored_write_id}},
        {"Bucket": BUCKET, "Key": KEY},
    )
    stubber.activate()
    return R2RawStore(BUCKET, client, new_write_id=lambda: "w-this-call"), stubber


def test_r2_put_once_treats_412_on_its_own_retried_write_as_success():
    # Sample: a 290 MB upload whose response is lost to a timeout, so boto3 retries and R2
    # answers 412. The stored object carries this call's write id, so the first attempt
    # did commit. Cloudflare documents this case; it must not fail the run.
    store, stubber = r2_conflict_store("w-this-call")
    assert store.put_once(KEY, PAYLOAD) == PAYLOAD_SHA
    stubber.assert_no_pending_responses()


def test_r2_put_once_raises_when_412_object_came_from_another_write():
    # Sample: 412 on a key whose object was written by an earlier run (different write id).
    store, _ = r2_conflict_store("an-earlier-run")
    with pytest.raises(KeyExistsError):
        store.put_once(KEY, PAYLOAD)
