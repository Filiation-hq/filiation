# ADR-008: Scheduled batch ingestion over live ingestion

**Date:** 2026-11-11
**Status:** Proposed (accepted in conversation by Steven Rocca; pending DS review)
**Author:** Steven Rocca

## Decision
Ingest openFDA and CDRH data in a weekly scheduled batch run on GitHub Actions. Each run is incremental, idempotent, and published as a versioned snapshot. No live or streaming ingestion.

## Context
Volume verified on 2026-11-11 against `api.fda.gov/device/510k.json`:
- 176,625 total 510(k) records.
- Clearances per `decision_date` year: 2021: 3,023; 2022: 3,209; 2023: 3,346; 2024: 3,129; 2025: 3,225. That is about 12–13 per business day.
- `meta.last_updated` was 2026-11-02 for 510(k)s and 2026-11-07 for recalls. The refresh cadence is **not verified** from these two samples; Stage 1 logs `last_updated` on every run to measure it.

Source data lags as well: summary PDFs post after clearance, and recall classification trails distribution by months. A live feed cannot be fresher than its slowest source.

## Options Considered
1. **Scheduled batch (GitHub Actions cron)**: weekly incremental pull keyed on `decision_date`, then extract and publish a release.
2. **Batch with an orchestrator (Dagster or Prefect)**: same model, with per-step retries, lineage, and asset materialization.
3. **Live or streaming (polling worker or queue, e.g. Kafka)**: continuous ingestion as records appear.

## Tradeoffs
| Criterion | Cron batch | Orchestrated batch | Live |
|---|---|---|---|
| Fits ~3k records/year | Yes | Yes | Heavily oversized |
| Reproducibility | Versioned snapshot per run | Versioned snapshot per run | Continuously changing state |
| Incomplete records (PDF not yet posted) | Picked up on a later run | Picked up on a later run | Ingested incomplete; needs re-fetch logic |
| Gold-set evaluation | Frozen snapshot | Frozen snapshot | Moving target |
| New infrastructure | None (reuses CI and Makefile) | Orchestrator deployment | Queue, worker, monitoring |
| Industry norm for public regulatory data | Yes | Yes, at larger scale | Uncommon |

## Consequences
- Stage 1 adds a scheduled workflow and incremental, idempotent fetch logic. Amended FDA records are handled by diffing each release against the previous one.
- Revisit Option 2 when the pipeline has enough dependent steps that per-step retries or lineage are needed, expected in Stage 3 (`docs/ROADMAP.md`).
- Does not change data contracts or the 3-tier data layout.

## Definitions Altered
- None.
