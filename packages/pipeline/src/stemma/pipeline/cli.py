"""Unified CLI for the Stemma data engineering pipeline."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import click
import pandas as pd
from stemma.core.data_loader import StemmaDataLoader
from stemma.core.schemas.device_nodes import DeviceNode
from stemma.core.storage.raw_store import raw_store_from_env
from stemma.core.utils.paths import get_derived_dir, get_interim_dir, get_raw_dir
from stemma.pipeline.crawler.cdrh_crawler import CDRHCrawler
from stemma.pipeline.crawler.ingestion_run import run_ingestion
from stemma.pipeline.crawler.openfda_bulk import SOURCES, default_opener
from stemma.pipeline.crawler.openfda_client import OpenFDAClient
from stemma.pipeline.crawler.sampler import PilotSampler
from stemma.pipeline.extraction.parser import SummaryParser

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("stemma.pipeline")


@click.group()
def cli() -> None:
    """Stemma Pipeline: Data Engineering for FDA 510(k) Lineage Graph."""
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


def _head_commit() -> str:
    """Returns the SHA of the checked-out commit.

    Returns:
        str: Full 40-character SHA from `git rev-parse HEAD`.

    Raises:
        click.UsageError: If git is unavailable or this is not a repository.
    """
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise click.UsageError("Pass --commit-sha; git rev-parse HEAD failed") from exc


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
@click.option("--run-id", envvar="GITHUB_RUN_ID", default=None, help="Run id suffix [env: GITHUB_RUN_ID]")
@click.option(
    "--run-attempt",
    envvar="GITHUB_RUN_ATTEMPT",
    default=None,
    help="Re-run attempt, appended to the run id [env: GITHUB_RUN_ATTEMPT]",
)
@click.option("--commit-sha", envvar="GITHUB_SHA", default=None, help="Code commit [env: GITHUB_SHA]")
@click.option(
    "--writer",
    type=click.Choice(["github-actions", "oracle-vm", "local"]),
    default=None,
    help="Who is writing; defaults to github-actions under Actions, else local",
)
def ingest_openfda(
    sources: tuple[str, ...],
    run_id: str | None,
    run_attempt: str | None,
    commit_sha: str | None,
    writer: str | None,
) -> None:
    """Stores the current openFDA exports write-once and records the run in a manifest."""
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    # GitHub keeps GITHUB_RUN_ID on "Re-run jobs"; the attempt keeps manifest keys unique.
    suffix = f"{run_id}-{run_attempt}" if run_id and run_attempt else run_id
    manifest = run_ingestion(
        list(sources),
        raw_store_from_env(),
        run_suffix=suffix or f"local-{int(time.time())}",
        commit_sha=commit_sha or _head_commit(),
        writer=writer or ("github-actions" if in_actions else "local"),
        opener=default_opener,
    )
    click.echo(manifest.to_json().decode("utf-8"))


@cli.command("sample-pilot")
@click.option("--size", default=500, help="Total pilot sample size across eras")
@click.option("--input", "input_path", type=click.Path(), default=None, help="Input 510(k) records JSON")
@click.option("--output", type=click.Path(), default=None, help="Output manifest JSON path")
def sample_pilot(size: int, input_path: str | None, output: str | None) -> None:
    """Generates stratified pilot sample manifest across the 5 regulatory eras."""
    client = OpenFDAClient()
    sampler = PilotSampler()

    if input_path and Path(input_path).exists():
        with open(input_path, encoding="utf-8") as f:
            raw_data = json.load(f)
        nodes = [DeviceNode.from_dict(d) for d in raw_data]
    else:
        logger.info("Fetching sample of 510(k) records from openFDA for pilot stratification...")
        # Fetch a batch from openFDA to stratify
        nodes = client.fetch_510k_records(limit=100)

    manifest = sampler.sample_nodes(nodes, total_sample_size=size)
    out_file = Path(output) if output else (get_interim_dir() / "pilot_manifest.json")
    saved = sampler.save_manifest(manifest, out_file)
    logger.info("Generated pilot manifest with %d devices across eras: %s", len(manifest), saved)


@cli.command("crawl-pdfs")
@click.option("--manifest", type=click.Path(), default=None, help="Pilot manifest JSON path")
@click.option("--k-number", default=None, help="Specific K-number to fetch")
@click.option("--rate-limit", default=1.0, help="Polite delay between requests in seconds")
@click.option("--dest", type=click.Path(), default=None, help="Destination directory for raw PDFs")
def crawl_pdfs(manifest: str | None, k_number: str | None, rate_limit: float, dest: str | None) -> None:
    """Crawls 510(k) summary PDFs from FDA CDRH with rate-limiting."""
    dest_path = Path(dest) if dest else (get_raw_dir() / "pdfs")
    crawler = CDRHCrawler(dest_dir=dest_path, rate_limit_seconds=rate_limit)

    targets = []
    if k_number:
        targets.append(k_number)
    elif manifest and Path(manifest).exists():
        with open(manifest, encoding="utf-8") as f:
            items = json.load(f)
        targets = [item["k_number"] for item in items if "k_number" in item]
    else:
        logger.error("Must provide either --k-number or --manifest")
        return

    logger.info("Beginning polite crawl for %d device summaries (rate limit: %.1fs)...", len(targets), rate_limit)
    success = 0
    for k in targets:
        res = crawler.fetch_pdf(k)
        if res.get("status") in ("DOWNLOADED", "CACHED"):
            success += 1
            logger.info("[%s] %s -> %s", res["status"], k, res.get("path"))
        else:
            logger.warning("[FAILED] %s: %s", k, res.get("error"))

    logger.info("Crawl complete. Successfully fetched/cached: %d/%d", success, len(targets))


@cli.command("extract")
@click.option("--input", "input_dir", type=click.Path(), default=None, help="Directory containing raw PDFs")
@click.option("--output", type=click.Path(), default=None, help="Output JSON path for extracted edges")
def extract(input_dir: str | None, output: str | None) -> None:
    """Extracts predicate relationships from PDFs with page citations and confidence scores."""
    in_path = Path(input_dir) if input_dir else (get_raw_dir() / "pdfs")
    out_path = Path(output) if output else (get_interim_dir() / "extracted_edges.json")

    pdf_files = list(in_path.glob("*.pdf"))
    logger.info("Found %d PDFs in %s. Running extraction parser...", len(pdf_files), in_path)

    parser = SummaryParser()
    all_edges = []

    for pdf in pdf_files:
        edges = parser.parse_pdf(pdf)
        for e in edges:
            all_edges.append(e.to_dict())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(all_edges, f, indent=2)

    logger.info("Extracted %d predicate edges across %d PDFs -> %s", len(all_edges), len(pdf_files), out_path)


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


@cli.command("status")
def status() -> None:
    """Displays local data store health and file counts."""
    loader = StemmaDataLoader()
    summary = loader.summary()
    click.echo("=== Stemma Data Engineering Status ===")
    click.echo(json.dumps(summary, indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
