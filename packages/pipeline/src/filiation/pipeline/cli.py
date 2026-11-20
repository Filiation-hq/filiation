"""Unified CLI for the Filiation data engineering pipeline.

The public commands live here. Internal commands (PDF crawl, extraction, recall report) are added
from filiation.pipeline.cli_internal when it is installed; the public mirror does not ship it.
"""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import click
import pandas as pd
from filiation.core.data_loader import FiliationDataLoader
from filiation.core.storage.raw_store import raw_store_from_env
from filiation.core.utils.paths import get_derived_dir, get_interim_dir, get_raw_dir
from filiation.pipeline.cli_common import (
    commit_option,
    run_attempt_option,
    run_id_option,
    run_identity,
    writer_option,
)
from filiation.pipeline.crawler.amendment_report import amendment_report as build_amendment_report
from filiation.pipeline.crawler.amendment_report import report_markdown
from filiation.pipeline.crawler.ingestion_run import run_ingestion
from filiation.pipeline.crawler.openfda_bulk import SOURCES, default_opener
from filiation.pipeline.crawler.openfda_client import OpenFDAClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("filiation.pipeline")
INTERNAL_COMMANDS = "filiation.pipeline.cli_internal"


@click.group()
def cli() -> None:
    """Filiation Pipeline: Data Engineering for FDA 510(k) Lineage Graph."""
    pass


@cli.command("fetch-openfda")
@click.option("--limit", default=100, help="Number of records to fetch from openFDA")
@click.option("--recalls", is_flag=True, help="Fetch recall actions instead of 510(k) clearances")
@click.option("--output", type=click.Path(), default=None, help="Output JSON path")
def fetch_openfda(limit: int, recalls: bool, output: str | None) -> None:
    """Fetches clearance or recall records from openFDA."""
    client = OpenFDAClient()
    if recalls:
        logger.info("Fetching %d recall records from openFDA...", limit)
        records = client.fetch_recalls(limit=limit)
        data = [r.to_dict() for r in records]
        default_out = get_raw_dir() / "openfda" / "sample_recalls.json"
    else:
        logger.info("Fetching %d 510(k) clearance records from openFDA...", limit)
        records = client.fetch_510k_records(limit=limit)
        data = [r.to_dict() for r in records]
        default_out = get_raw_dir() / "openfda" / "sample_510k.json"

    out_path = Path(output) if output else default_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    logger.info("Saved %d records to %s", len(data), out_path)


@cli.command("ingest-openfda")
@click.option(
    "--source",
    "sources",
    type=click.Choice(sorted(SOURCES)),
    multiple=True,
    default=sorted(SOURCES),
    show_default=True,
    help="openFDA export(s) to ingest; repeat for several",
)
@run_id_option
@run_attempt_option
@commit_option
@writer_option
def ingest_openfda(
    sources: tuple[str, ...],
    run_id: str | None,
    run_attempt: str | None,
    commit_sha: str | None,
    writer: str | None,
) -> None:
    """Stores the current openFDA exports write-once and records the run in a manifest."""
    suffix, commit, who = run_identity(run_id, run_attempt, commit_sha, writer)
    manifest = run_ingestion(
        list(sources), raw_store_from_env(), run_suffix=suffix, commit_sha=commit, writer=who, opener=default_opener
    )
    click.echo(manifest.to_json().decode("utf-8"))


@cli.command("export-derived")
@click.option("--edges", type=click.Path(), default=None, help="Extracted edges JSON path")
@click.option("--dest", type=click.Path(), default=None, help="Target releases directory")
def export_derived(edges: str | None, dest: str | None) -> None:
    """Compiles extracted edges and nodes into versioned Parquet and CSV files in data/derived/."""
    edges_file = Path(edges) if edges else (get_interim_dir() / "extracted_edges.json")
    target_dir = Path(dest) if dest else (get_derived_dir() / "releases")
    target_dir.mkdir(parents=True, exist_ok=True)

    if not edges_file.exists():
        logger.error("Edges file %s does not exist. Run 'extract' first.", edges_file)
        return

    with edges_file.open(encoding="utf-8") as f:
        raw_edges = json.load(f)

    if not raw_edges:
        logger.warning("No edges found in %s", edges_file)
        return

    df_edges = pd.DataFrame(raw_edges)
    csv_path = target_dir / "predicate_edges.csv"
    parquet_path = target_dir / "predicate_edges.parquet"

    df_edges.to_csv(csv_path, index=False)
    try:
        df_edges.to_parquet(parquet_path, index=False)
        logger.info("Exported %d edges to %s and %s", len(df_edges), parquet_path, csv_path)
    except Exception as e:
        logger.info("Exported %d edges to %s (parquet export skipped: %s)", len(df_edges), csv_path, e)


@cli.command("amendment-report")
@click.option("--format", "fmt", type=click.Choice(["json", "markdown"]), default="json", show_default=True)
def amendment_report(fmt: str) -> None:
    """Prints how often openFDA amended and removed records across the stored runs (#91, ADR-021)."""
    report = build_amendment_report(raw_store_from_env())
    if fmt == "markdown":
        click.echo(report_markdown(report), nl=False)
    else:
        click.echo(json.dumps(report.to_dict(), indent=2))


@cli.command("status")
def status() -> None:
    """Displays local data store health and file counts."""
    loader = FiliationDataLoader()
    summary = loader.summary()
    click.echo("=== Filiation Data Engineering Status ===")
    click.echo(json.dumps(summary, indent=2))


def register_internal_commands(
    group: click.Group, import_module: Callable[[str], ModuleType] = importlib.import_module
) -> bool:
    """Adds the internal commands to the CLI if their module is installed.

    Args:
        group: The click group to add commands to.
        import_module: Imports a module by name; replaced in tests.

    Returns:
        bool: True if the internal commands were added, False if their module is absent.

    Raises:
        ModuleNotFoundError: If the internal module is installed but a module it needs is not.
    """
    try:
        module = import_module(INTERNAL_COMMANDS)
    except ModuleNotFoundError as exc:
        if exc.name != INTERNAL_COMMANDS:
            raise
        return False
    module.register(group)
    return True


register_internal_commands(cli)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
