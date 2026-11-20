# ADR-018: Store each run's per-key changes as a write-once change list linked from the manifest (schema v3)

**Date:** 2026-11-19
**Status:** Accepted
**Author:** Steven Rocca

## Decision
When a source's export differs from the previous snapshot, the run stores which keys changed. The list goes in `runs/<run_id>/diffs/<source>.jsonl.gz`: gzip JSON Lines with one line per added, removed or amended key, sorted by key. An amended line lists the record's changed top-level fields. Run manifest schema v3 links the list from `SourceDiff.changes`, with its SHA-256, size and line count. The diff counts are derived from the same list, so the two cannot disagree.

## Context
#76: the weekly diff (#40) stored only counts. Incremental extraction, recall linkage, and the amendment measurement for R01 and R03 (#91) need the keys themselves. Every snapshot is already stored, so a reader can recover old and new values for any listed key from the two exports.

Measured on the real 510(k) exports of 2026-11-09 and 2026-11-18 (176,625 and 176,683 records):
- **171,415 keys changed:** 58 added, 0 removed and 171,357 amended. Every amended record changed `openfda`. Only 6 field changes fell outside it: `advisory_committee` and its description on 3 records, plus `address_2`, `contact` and `product_code` once each.
- **The `openfda` churn is all in two fields:** `registration_number` and `fei_number`. In 5,000 sampled records, 4,849 changed. About half had the same entries reordered and half had entries added or dropped. Weekly "amended" counts therefore measure openFDA's re-enrichment of establishment lists, not FDA amending records.
- **Size:** the list is 9.9 MB uncompressed and 0.45 MB gzipped.
- **Worst cases:**
  - All 176,683 records amended in all 24 top-level fields: 76.5 MB raw, 0.67 MB gzipped.
  - Every record amended in `openfda` only: 0.46 MB.
- **Cost:** the diff took 147 s with a 1.3 GB peak RSS. That is the same two-pass stream as the counts-only diff; encoding adds 1.2 s.

## Options Considered
1. **A gzip JSON Lines list per source under `runs/<run_id>/diffs/`, linked from the manifest (v3).**
2. **Inline key lists in the manifest.** This would add about 10 MB of JSON to every weekly manifest, and `latest.json` readers load the manifest every run.
3. **Store old and new values per changed field.** Values are already in both stored exports, and the `openfda` lists would make this the largest object of the run.

## Tradeoffs
| Criterion | 1: linked list | 2: inline | 3: values |
|---|---|---|---|
| Manifest size | +1 object entry | +~10 MB | +1 object entry |
| Stored size, real week | 0.45 MB | ~10 MB | Several MB at least: every amended record's `openfda` lists before and after |
| Verifiable | SHA-256 in the manifest | Part of the manifest | SHA-256 |
| Reader effort for values | Read both exports for listed keys | Same | None |

## Consequences
- **Core contract:** `run_manifest.py` gains `KeyChange`, `encode_changes`, `decode_changes`, `changes_key` and `SourceDiff.changes`. `SCHEMA_VERSION` becomes 3.
- **Versioning:** the hard-coded v2 rule becomes a table of the version that introduced each optional field (`SOURCE_FIELDS_SINCE`, `DIFF_FIELDS_SINCE`). The next bump adds one entry.
- **Manifest validation:**
  - v1 and v2 manifests still load and are written back without fields they could not have held. A v2 document that carries a change list is rejected.
  - A v3 diff with changes must link its list, under its own run's `diffs/` key. A zero diff links none.
  - The list's line count must equal added + removed + amended.
- **Determinism:** equal changes encode to equal bytes. Lines are sorted, JSON is compact UTF-8, and the gzip header has `mtime` 0 and no file name. The bytes could still change if Python's zlib changes its output, so readers verify by SHA-256 and never rely on byte equality across Python versions.
- **What gets stored:** no list is written when nothing changed, or when there is no previous snapshot.
- **Reruns:** a rerun with the same run id after a crash reuses an identical list already at the key. A different list there is refused (`KeyExistsError`), since nothing is overwritten. If a crash happens after the list is stored but before the manifest, the list is an orphan, covered by #89. GitHub re-runs get a new run id anyway (`<run>-<attempt>`).
- **Reader:** `decode_changes` rejects input that is not gzip, is truncated, has a malformed line, or has keys out of order.
- **For #91:** it must report `openfda` changes apart from changes to FDA's own fields. Otherwise the amendment rate is about 97% a week and means nothing for R01.
- Not changed: how records are matched or digested (#40), raw objects, `latest.json`.

## Definitions Altered
- `CONTEXT.md`: **run manifest** (schema v3), **snapshot diff**, and a new term, **change list**.
- ADR-010's key layout gains `runs/<run_id>/diffs/<source>.jsonl.gz`.
- Sign-off: the developer accepted this on 2026-11-19. The data science role is unassigned, so its review is pending (AGENTS.md §13).
