"""Unit tests for run manifest schema v4: change lists that mark cosmetic field changes (#91, ADR-021).

Task map:
    Input: an amended KeyChange's changed top-level fields, plus the subset of them whose change
        is cosmetic only (whitespace inside text, or the order of list entries).
    Output: change-list lines that carry "cosmetic" only when some field is cosmetic, that read
        back to the same changes; new manifests at schema v4, whose lists are classified.
    Edge cases: a cosmetic field the change doesn't list as changed; cosmetic fields on an added
        or removed key; duplicate or unsorted cosmetic names; a v3 line with no "cosmetic" key
        (reads as none, since v3 never classified); every changed field cosmetic; a v3 manifest
        loaded and written back; schema_version 5.
    Invariants: cosmetic is always a subset of fields; substantive_fields is fields minus
        cosmetic; equal changes still encode to equal bytes.
New names are reached through the module so this file imports on the base branch (#137).
"""

import gzip
import json

import pytest
from filiation.core.schemas import run_manifest as rm
from filiation.core.schemas.run_manifest import ManifestObject, RunManifest, SourceSnapshot

SHA = "eb479de53bcb8b48c73c9a5ecaf6eea75eb84a00431ac0c04000090005c663c9"
COMMIT = "f7d257a" + "0" * 33
KEY = "openfda/recall/export_date=2026-11-18/device-recall-0001-of-0001.json.zip"


def manifest(version: int | None = None) -> RunManifest:
    """Builds a one-source recall manifest with no diff.

    Args:
        version: schema_version to set; None takes the default.

    Returns:
        RunManifest: The manifest.
    """
    extra = {} if version is None else {"schema_version": version}
    source = SourceSnapshot("recall", "stored", [ManifestObject(KEY, SHA, 1000, 3)], "2026-11-18")
    return RunManifest(
        run_id="2026-11-23_102",
        run_date="2026-11-23",
        started_at="2026-11-23T06:00:00Z",
        finished_at="2026-11-23T06:01:00Z",
        commit_sha=COMMIT,
        writer="github-actions",
        sources=[source],
        **extra,
    )


def test_key_change_keeps_cosmetic_fields_sorted_and_unique():
    # Sample: recall Z-0636-2013 from the real Nov 13 -> 18 diff: openfda re-enriched, and
    # product_description and action changed only in whitespace, given unsorted with a repeat.
    change = rm.KeyChange(
        "Z-0636-2013", "amended", ("openfda", "product_description", "action"), cosmetic=("product_description", "action", "action")
    )
    assert change.cosmetic == ("action", "product_description")


def test_key_change_substantive_fields_leave_out_cosmetic_ones():
    # Sample: the same recall. Only openfda changed in substance.
    change = rm.KeyChange("Z-0636-2013", "amended", ("action", "openfda", "product_description"), cosmetic=("action", "product_description"))
    assert change.substantive_fields == ("openfda",)


def test_key_change_with_every_field_cosmetic_has_no_substantive_fields():
    # Sample: a 510(k) whose openfda fei_number list was only reordered, the most common real
    # weekly change. It is still an amendment, with nothing substantive.
    change = rm.KeyChange("K210758", "amended", ("openfda",), cosmetic=("openfda",))
    assert change.substantive_fields == ()


def test_key_change_rejects_a_cosmetic_field_it_does_not_list_as_changed():
    # Sample: cosmetic names "action", but only openfda changed. A field can't be a cosmetic
    # change without being a change.
    with pytest.raises(ValueError, match="cosmetic"):
        rm.KeyChange("Z-0636-2013", "amended", ("openfda",), cosmetic=("action",))


@pytest.mark.parametrize("kind", ["added", "removed"])
def test_key_change_rejects_cosmetic_fields_on_an_added_or_removed_key(kind):
    # Sample: a new or vanished recall. Only an amendment compares fields.
    with pytest.raises(ValueError, match="cosmetic"):
        rm.KeyChange("Z-0001-2026", kind, cosmetic=("action",))


def test_change_list_line_carries_cosmetic_only_when_some_field_is_cosmetic():
    # Sample: one amendment with a cosmetic field and one without (Z-0774-03, blanked to "n/a").
    data = rm.encode_changes(
        [
            rm.KeyChange("Z-0636-2013", "amended", ("action", "openfda"), cosmetic=("action",)),
            rm.KeyChange("Z-0774-03", "amended", ("code_info", "product_description")),
        ]
    )
    lines = [json.loads(line) for line in gzip.decompress(data).decode("utf-8").splitlines()]
    assert lines == [
        {"key": "Z-0636-2013", "change": "amended", "fields": ["action", "openfda"], "cosmetic": ["action"]},
        {"key": "Z-0774-03", "change": "amended", "fields": ["code_info", "product_description"]},
    ]


def test_change_list_reads_back_cosmetic_fields():
    # Sample: the same two changes; decoding must give them back unchanged.
    changes = [
        rm.KeyChange("Z-0636-2013", "amended", ("action", "openfda"), cosmetic=("action",)),
        rm.KeyChange("Z-0774-03", "amended", ("code_info", "product_description")),
    ]
    assert rm.decode_changes(rm.encode_changes(changes)) == changes


def test_change_list_written_by_v3_reads_with_no_cosmetic_fields():
    # Sample: a v3 line, which never had a "cosmetic" key. It reads back with none; whether that
    # means "not classified" is decided by the manifest's schema_version, not the line.
    line = json.dumps({"key": "K210758", "change": "amended", "fields": ["openfda"]}) + "\n"
    (change,) = rm.decode_changes(gzip.compress(line.encode("utf-8")))
    assert change.cosmetic == ()


def test_change_list_rejects_a_line_whose_cosmetic_field_is_not_a_changed_field():
    # Sample: a hand-edited or corrupted line claiming "action" is cosmetic while only openfda changed.
    line = json.dumps({"key": "K210758", "change": "amended", "fields": ["openfda"], "cosmetic": ["action"]}) + "\n"
    with pytest.raises(ValueError, match="line 1"):
        rm.decode_changes(gzip.compress(line.encode("utf-8")))


def test_new_manifests_are_schema_v4_so_their_change_lists_count_as_classified():
    # Sample: a manifest built with no explicit version, as every new run does.
    assert manifest().schema_version == 4
    assert rm.COSMETIC_SINCE == 4


def test_a_v3_manifest_still_loads_and_keeps_its_version():
    # Sample: the 2026-11-20 run's manifest, written at v3 before this change.
    data = manifest(version=3).to_dict()
    assert RunManifest.from_dict(data).schema_version == 3


def test_schema_version_5_is_rejected():
    # Sample: a manifest from a future schema this code can't read.
    with pytest.raises(ValueError, match="schema_version"):
        manifest(version=5)
