"""Unit tests for the public filiation-pipeline CLI and how it loads the internal commands.

Task map:
    Input: the click group in filiation.pipeline.cli, and the module that adds internal commands.
    Output: the public commands always load; the internal ones (PDF crawl, extraction, recall
        report) load only when filiation.pipeline.cli_internal is installed, which it is not in
        the public mirror.
    Edge cases: the internal module is absent (public mirror) -> skipped quietly; a module the
        internal module needs is missing (e.g. pdfplumber without the pdf extra) -> the error
        propagates rather than hiding commands; cli.py or cli_common.py importing anything
        outside the public modules -> caught here before it breaks the mirror.
    Invariant: the public CLI never imports a module the public mirror does not ship.

amendment-report (#91): prints the amendment and removal report for the configured store, as
JSON by default or Markdown for the job summary; an empty store still prints a valid report.

Tests for the internal commands live in test_cli_internal.py.
"""

import ast
from pathlib import Path

import click
import pytest
from filiation.pipeline.cli import INTERNAL_COMMANDS, register_internal_commands

PIPELINE_SRC = Path(__file__).resolve().parents[2] / "packages" / "pipeline" / "src" / "filiation" / "pipeline"
PUBLIC_IMPORTS = (
    "filiation.core.",
    "filiation.pipeline.cli_common",
    "filiation.pipeline.crawler.openfda_bulk",
    "filiation.pipeline.crawler.export_diff",
    "filiation.pipeline.crawler.ingestion_run",
    "filiation.pipeline.crawler.openfda_client",
    "filiation.pipeline.crawler.amendment_report",
)


def importer_raising(name: str):
    """Builds an import function that fails as if the named module were not installed.

    Args:
        name: The module name the ModuleNotFoundError reports.

    Returns:
        Callable[[str], object]: A stand-in for importlib.import_module.
    """

    def import_module(module: str) -> object:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)

    return import_module


def test_register_internal_commands_skips_quietly_when_the_internal_module_is_absent():
    # Sample: the public mirror, where filiation.pipeline.cli_internal is not shipped. The
    # public commands must still load, so a missing internal module is not an error.
    group = click.Group()
    assert register_internal_commands(group, import_module=importer_raising(INTERNAL_COMMANDS)) is False
    assert group.commands == {}


def test_register_internal_commands_reraises_when_a_module_the_internal_commands_need_is_missing():
    # Sample: cli_internal is installed but pdfplumber is not (pipeline installed without the
    # pdf extra). Hiding the commands would look like the public mirror; it must fail loudly.
    with pytest.raises(ModuleNotFoundError, match="pdfplumber"):
        register_internal_commands(click.Group(), import_module=importer_raising("pdfplumber"))


def test_register_internal_commands_adds_what_the_internal_module_registers():
    # Sample: a stand-in internal module whose register() adds one command, "crawl-pdfs".
    class Internal:
        @staticmethod
        def register(group: click.Group) -> None:
            group.add_command(click.Command("crawl-pdfs"))

    group = click.Group()
    assert register_internal_commands(group, import_module=lambda name: Internal) is True
    assert list(group.commands) == ["crawl-pdfs"]


@pytest.mark.parametrize("module", ["cli.py", "cli_common.py"])
def test_public_cli_modules_import_only_modules_the_public_mirror_ships(module):
    # Sample: the import statements of cli.py and cli_common.py, parsed with ast. Each
    # filiation.* import must be core or one of the public pipeline modules; anything else
    # (CDRH crawler, extraction, linkage) would make the public CLI fail to import.
    tree = ast.parse((PIPELINE_SRC / module).read_text(encoding="utf-8"))
    imported = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
    imported += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    private = [name for name in imported if name.startswith("filiation.") and not name.startswith(PUBLIC_IMPORTS)]
    assert private == []


def run_amendment_report(tmp_path, monkeypatch, *args):
    """Runs `filiation-pipeline amendment-report` against an empty local store.

    Args:
        tmp_path: Directory for the store.
        monkeypatch: Replaces the store the command opens.
        *args: Extra command-line arguments.

    Returns:
        click.testing.Result: The command's result.
    """
    from click.testing import CliRunner
    from filiation.core.storage.raw_store import LocalRawStore
    from filiation.pipeline import cli as cli_module

    monkeypatch.setattr(cli_module, "raw_store_from_env", lambda: LocalRawStore(tmp_path / "store"))
    return CliRunner().invoke(cli_module.cli, ["amendment-report", *args])


def test_amendment_report_command_prints_json_by_default(tmp_path, monkeypatch):
    # Sample: a store no run has written to. The JSON report is still valid, with nothing in it.
    import json

    result = run_amendment_report(tmp_path, monkeypatch)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {"since": {}, "rows": [], "removals": []}


def test_amendment_report_command_prints_markdown_for_the_job_summary(tmp_path, monkeypatch):
    # Sample: the same empty store, rendered the way the weekly workflow writes it.
    result = run_amendment_report(tmp_path, monkeypatch, "--format", "markdown")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("## openFDA amendments and removals")
