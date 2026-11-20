# ADR-022: Check every export's record identity and field types before storing it

**Date:** 2026-11-20
**Status:** Accepted
**Author:** Steven Rocca

## Decision
Every openFDA export goes through identity checks in the same streaming pass that reconciles its counts, before anything is stored. A record with no usable key, a key that appears twice (identical or not), or a declared identity, date or linkage field with the wrong type fails the run. A record keyed by its fallback field and a top-level field missing from the 2026-11 exports are reported, and the run continues.

## Context
Count reconciliation (ADR-015) can't see a swap: if one key disappears and another appears twice, every count still matches. Any keyed step after that (the diff, the class join, a notebook indexing by key) collapses the duplicate and silently loses an observation. Before this change:
- The weekly diff rejected duplicate and unkeyable records, but only when the previous manifest held the same source. A source's first snapshot, or a newly added source, was never checked.
- The diff stopped at the first duplicate, didn't say whether the copies were identical, and named the file but not the row.
- Nothing checked types. `RecallRecord.from_openfda` reads `k_numbers or []`, so a `k_numbers` sent as the string `"K000001"` would become seven one-character K-numbers.

Measured on the 2026-11-18 510(k) (176,683 records), 2026-11-20 recall (59,705) and 2026-11-19 enforcement (40,535) exports: every product-level key is unique, enforcement has one blank `recall_number` (event 94520, keyed by `event_id`), no field is ever null, `k_numbers` and `pma_numbers` are always lists of strings, and every other field is a string. On those exports the checks find nothing but that one fallback key. They add about 13 s to the 510(k) pass and 12 s to the recall pass (hashing each record), and the whole run peaked at 169 MB.

## Options Considered
1. **Check at ingestion, fail on any repeat.** Run the checks on every export before storing it; an exact repeat fails like a conflicting one.
2. **Check at ingestion, report exact repeats.** Same, but an identical repeat is only reported, and downstream code collapses it (as ADR-017's label builder already does).
3. **Keep relying on the diff.** Extend the diff's error messages, and run it with an empty previous snapshot on a first run.

## Tradeoffs
| Criterion | Option 1 | Option 2 | Option 3 |
|---|---|---|---|
| Catches the swap that count checks pass | Yes | Only by reading a report | Yes, from the second run on |
| First snapshot of a source | Checked | Checked | Only with a special case |
| Exact repeat downstream | Never stored | Stored; every consumer must collapse it | Stored on first run |
| False failures on 2026-11 data | None | None | None |
| Cost | About 25 s a week | Same | None extra |

Option 1: an identical repeat has never occurred, and in a count-reconciled export one usually means another record was lost. Option 2 would leave that loss in a report nobody has to read.

## Consequences
- New module `packages/pipeline/src/filiation/pipeline/crawler/identity.py`: `IdentityProfile`, the check specs, `record_key`, and the per-source `RECORD_KEYS`, `FALLBACK_KEYS`, `KNOWN_FIELDS` and `FIELD_TYPES` tables. The diff keys records with the same `record_key`, so the two can't disagree about a record's key.
- `ingest_export` raises `ExportIntegrityError` with the identity findings, naming the partition and row, before any partition is stored, so `latest.json` and the run manifest are untouched.
- A whitespace-only key now falls back (enforcement) or fails, where the diff used to accept it as a key. No export has had one.
- `identity.fallback_key` will appear as a report finding on every enforcement run while event 94520's record stays blank.
- A field openFDA adds is reported (`identity.unexpected_field`) until it is added to `KNOWN_FIELDS`. A declared field openFDA renames is not yet detected as missing; that is #172.
- Unchanged: the run manifest schema (v4), ADR-017's label builder, which still collapses identical repeats it might meet in other inputs, and the diff's own duplicate check, which stays as a second guard.

## Definitions Altered
- `CONTEXT.md`: adds **Identity check**, and **Check** lists an `identity.*` example.
