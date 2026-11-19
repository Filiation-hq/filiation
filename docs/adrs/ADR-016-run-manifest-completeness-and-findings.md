# ADR-016: Record field completeness, checks run, and report findings in the run manifest (schema v2)

**Date:** 2026-11-19
**Status:** Accepted
**Author:** Steven Rocca

## Decision
Run manifest schema v2 adds three fields to each source: `fields_present`, `checks`, and `findings`. `fields_present` counts the records that hold a non-empty value at each JSON-pointer field path. `checks` lists every check id that ran. `findings` holds the report-severity findings those checks produced. v1 manifests still load, with all three fields `None`, which means "not checked". Ingestion computes completeness and the 510(k) date rules in the same streaming pass that already counts records.

## Context
#92 is the Stage 1 control for R14: each run must report field completeness, and impossible dates must be reported loudly rather than dropped. ADR-015 added `fail` and `report` checks tied to bias-register factors, but `report` findings had nowhere durable to live. The developer chose the run manifest over a separate object, so each snapshot carries its own quality record.

Measured on 2026-11-19, before the rules were written:
- In the 510(k) export (176,625 records), both dates parse in every record. None falls after the export, and no decision comes before its receipt. Only 3 receipts fall before the Medical Device Amendments: K760001, K760002 and K760003, received 1976-05-26 and cleared 1976-07-26. These are the first 510(k)s, so **a pre-MDA receipt is valid; only a pre-MDA decision is impossible.**
- In the recall export (59,705 records), the `openfda` block is filled in 97.95% of records. The third-party claim cited for R14, that it is empty in every record, does not hold.

## Options Considered
1. **Run manifest v2.** Completeness, checks and findings go on each source entry.
2. **A separate write-once `runs/<run_id>/findings.json`.** The manifest is unchanged.

## Tradeoffs
| Criterion | Option 1 | Option 2 |
|---|---|---|
| One record per snapshot | Yes | Two objects to keep in step |
| Contract change | Yes: shared schema, version bump | None now; folding it in later would be one |
| Readers that need quality data | Read the manifest they already load | Must find and read a second object |
| Risk at rollout | v1 manifests in the store must still load | None |

## Consequences
- `SCHEMA_VERSION` is 2. `from_dict` accepts versions 1 and 2. It rejects anything else, including `"2"`, `True` and a missing version. A v1 manifest is written back as v1, without the new fields, so it never claims checks that did not run. A v1 document that carries v2 fields is rejected.
- `None` and empty differ on purpose: `checks: null` means never checked, and `findings: []` means checked and clean. The job summary shows "not checked" or "no findings" accordingly.
- A written manifest may hold only `report` findings. A `fail` finding stops the run before any manifest exists, so one in a manifest means a gate was bypassed, and the manifest is rejected.
- `ManifestFinding` in core uses the same keys as the pipeline's `Finding.to_dict`. Both validate through `check_finding_fields` in core, so the two cannot drift apart.
- New checks (all `report`, all R14):
  - **Completeness, every source:** `completeness.field_always_empty`, `completeness.field_cap` (more than 1,000 paths), `completeness.non_object_record`.
  - **510(k) dates:** `dates.unparseable` (strict `YYYY-MM-DD`; Python's `fromisoformat` would also accept `20200102`), `dates.decision_before_mda`, `dates.after_export` (compared with the export date, not the run date), `dates.decision_before_received`.
- Each finding gives a count and up to 5 example identifiers. It is one finding per check, so a bad export can't bloat the manifest, and the smallest identifiers are chosen so the output doesn't depend on record order.
- Recall dates are not re-checked here. ADR-013 already validates and flags them in linkage.
- Cost: the 510(k) pass takes about 20 s instead of 13 s, with peak memory unchanged at about 86 MB.
- Not changed: fail gates (ADR-015), raw objects, `latest.json`. Drift between runs is #146 and #91. The product-code join for class and specialty is #159.

## Definitions Altered
- `CONTEXT.md`: run manifest (schema v2); new terms **field completeness**, **check**, **finding**.
- `RunManifest.schema_version` is now validated on construction. `SourceSnapshot` gains `fields_present`, `checks`, and `findings`.
- Sign-off: the developer approved the contract change on 2026-11-19. The data science role is unassigned, so its review is pending (AGENTS.md §13).
