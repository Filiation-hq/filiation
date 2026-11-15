"""Unit tests for the stemma-pipeline click CLI commands.

Task map:
    Input: CLI arguments plus small JSON fixtures written to a temp dir.
    Output: exit code 0 and the expected output file.
    Edge cases: `sample-pilot --input` path, which deserializes DeviceNode records;
        `extract` over the raw-store PDF layout (cdrh/pdfs/<K-number>/<sha256>.pdf, #43),
        where the file name is a hash and the K-number is the folder.
    Invariant: no command touches the network when given local input.
"""

import json

from click.testing import CliRunner
from stemma.core.schemas.device_nodes import DeviceNode
from stemma.pipeline.cli import cli


def test_sample_pilot_with_input_file_writes_manifest_without_name_error(tmp_path):
    # Sample: one Era 5 DeviceNode (K210758, Stryker, product code MRN, 2021-04-15)
    # serialized to input.json. Before the fix, DeviceNode was not imported in cli.py,
    # so reading --input raised NameError.
    node = DeviceNode(
        k_number="K210758",
        applicant="Stryker Orthopaedics",
        device_name="Spine Nav",
        product_code="MRN",
        decision_date="2021-04-15",
        review_panel="OR",
    )
    input_file = tmp_path / "input.json"
    input_file.write_text(json.dumps([node.to_dict()]), encoding="utf-8")
    output_file = tmp_path / "manifest.json"

    result = CliRunner().invoke(
        cli, ["sample-pilot", "--input", str(input_file), "--output", str(output_file), "--size", "1"]
    )

    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0
    assert output_file.exists()


def test_extract_reads_the_raw_store_pdf_layout_and_takes_the_k_number_from_the_folder(tmp_path, monkeypatch):
    # Sample: two versions of K221020's summary and one of K994060, stored as
    # <dir>/<K-number>/<sha256>.pdf by crawl-pdfs. Each file must be parsed with its folder's
    # K-number as the subject, not the hash in its file name.
    import stemma.pipeline.cli as cli_module

    pdfs = tmp_path / "cdrh" / "pdfs"
    for k_number, sha in [("K221020", "a" * 64), ("K221020", "b" * 64), ("K994060", "c" * 64)]:
        (pdfs / k_number).mkdir(parents=True, exist_ok=True)
        (pdfs / k_number / f"{sha}.pdf").write_bytes(b"%PDF-1.4")
    calls = []
    monkeypatch.setattr(cli_module.SummaryParser, "parse_pdf", lambda self, path, subject_k=None: calls.append(
        (path.name[:1], subject_k)) or [])

    result = CliRunner().invoke(cli, ["extract", "--input", str(pdfs), "--output", str(tmp_path / "edges.json")])

    assert result.exit_code == 0, result.output
    assert sorted(calls) == [("a", "K221020"), ("b", "K221020"), ("c", "K994060")]
