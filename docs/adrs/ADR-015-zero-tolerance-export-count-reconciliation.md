# ADR-015: Reconcile export counts against the export's own metadata with zero tolerance

**Date:** 2026-11-19
**Status:** Accepted
**Author:** Steven Rocca

## Decision
An ingestion run stores an openFDA export only if every count that the export's own index entry and partition files state matches the number of records actually streamed, with zero tolerance. The live API's `meta.results.total` is not part of the check.

## Context
Stage 1 has an exit criterion that every snapshot's count must match the source (#46). Before this change, ingestion compared only each partition's streamed count with the `records` value that `download.json` lists for it. That misses a lost partition, a partition served twice, and partitions taken from two different export builds.

Checked against openFDA on 2026-11-19:
- In every partition, `meta.results` holds `skip` (the partition's offset), `limit` (its own record count; `device-udi-0052-of-0052` reports 82,695, not the 100,000 page size), and `total` (the whole export's count, repeated in every partition).
- For every source in `download.json`, `total_records` equals the sum of its partitions' `records`.
- The live API reported 176,683 510(k) records under `last_updated` 2026-11-09. The stored export with that same `last_updated` holds 176,625. The API refreshes on its own schedule, so neither `last_updated` nor the live total identifies a version of the data.

## Options Considered
1. **Zero tolerance against the export's own counts.** Check index `records` and `total_records`, and each partition's `meta.results`, against the streamed records.
2. **Tolerance against the live API's total.** Call `/device/<source>.json?limit=1` and allow a documented difference.

## Tradeoffs
| Criterion | Option 1 | Option 2 |
|---|---|---|
| False failures | None: every count comes from the same export job | Likely whenever the API and the bulk export refresh on different days |
| Catches a lost or duplicated partition | Yes, through skip tiling and the total | Only if the loss exceeds the tolerance |
| Network dependency | None beyond the download | One extra API call per source |
| Tolerance to justify | 0, by construction | An arbitrary number |

## Consequences
- `reconcile_export_counts` in `crawler/openfda_bulk.py` runs before anything is written. It returns every disagreement, not only the first, as findings. Any finding raises `ExportIntegrityError`, which carries those findings, and the run writes no manifest.
- Each check is a `CheckSpec` declared in `crawler/findings.py`. It has a stable dotted id (for example `export_counts.total`), a severity (`fail` stops the run; `report` records and continues), and the bias-register factors it controls. The count checks control R18, because a lost or doubled partition would otherwise look like upstream removals or additions in the weekly diff. A missing `meta.results` controls R14. A private test fails if a check cites a factor that the register lacks.
- Later Stage 1 controls declare their own `CheckSpec`s instead of adding new error types: #92's completeness report is `report` severity for R14, and its impossible-date rules get whichever severity #48 settles on. Recording `report` findings in the run manifest changes the `packages/core/schemas/` contract, so it belongs to #92 with both owners' agreement. `Finding.to_dict` already fixes the keys for that.
- A missing or malformed `meta.results` stops the run. If openFDA drops the block, the change is visible right away, and nothing gets stored unchecked.
- A missing `total_records` in `download.json` is tolerated, because `meta.results.total` still carries the total. A malformed or mismatched `total_records` raises `ValueError`.
- Duplicate top-level `meta` or `results` keys in an export now fail every reader.
- Reading snapshots that are already stored does not require `meta.results`.
- Drift between the live API and the export is not monitored. If that becomes useful, it should be a report, not a gate.

## Definitions Altered
- None in `CONTEXT.md`. `ExportListing` gains `total_records`, `ExportStats` gains `meta_results`, and `ExportIntegrityError` gains `findings`. All are pipeline-internal, not data contracts.
- New pipeline-internal terms: a **check** (a `CheckSpec`), its **severity**, and a **finding** (one check's result against one source or partition).
