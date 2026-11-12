"""Unit tests for the stemma-pipeline click CLI commands.

Task map:
    Input: CLI arguments plus small JSON fixtures written to a temp dir.
    Output: exit code 0 and the expected output file.
    Edge cases: `sample-pilot --input` path, which deserializes DeviceNode records.
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
