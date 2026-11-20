# ADR-021: Mark cosmetic field changes in change lists (schema v4) and report FDA edits apart from openFDA churn

**Date:** 2026-11-20
**Status:** Accepted
**Author:** Steven Rocca

## Decision
Each amended line in a change list also names the changed fields whose change is **cosmetic**: whitespace inside text, or the order of list entries, at any depth. Run manifest schema v4 marks that its change lists carry this; v3 lists never did. A new public command, `filiation-pipeline amendment-report`, reads every stored run and splits each source's amended keys into three kinds:
- **FDA edits:** a substantive change outside `openfda`.
- **`openfda` only:** openFDA re-enrichment and nothing else.
- **Cosmetic only.**

It counts FDA edits per field and per group (class, dates, identifiers, status or decision). It lists each removed key with the run that first missed it, and the run it returned in. The weekly workflow writes the report into its job summary.

## Context
#91 asks how often openFDA amends and removes records, for R01, R03 and R18. ADR-018 found that weekly 510(k) "amendments" are about 97% `openfda` churn. Splitting out `openfda` turned out not to be enough. In the real recall exports of 2026-11-13 and 2026-11-18 (59,705 records each):
- **19,706 recalls were amended, with 0 added and 0 removed.** 19,687 of the amended recalls changed `openfda`.
- **3,170 field changes outside `openfda` were whitespace only.** Every one differed only in runs of whitespace inside text: 1,777 of 1,778 `product_description` changes, all 1,121 `action`, 181 `reason_for_recall` and 78 `distribution_pattern` changes, 7 of 8 `code_info` changes and 6 `recalling_firm` changes.
- **All 54 `k_numbers` changes were the same K-numbers in a new order.**
- **One recall had a real FDA-side change.** On Z-0774-03, openFDA replaced the product description and the lot list with `"n/a"`, so the data degraded upstream.
- **Class, dates and `recall_status`:** none changed.

So a rate that splits out only `openfda` would count at least 1,778 recalls as FDA edits where there was one. Only the diff sees both values, so the diff is where to classify them.

Re-running the classified diff on the stored exports (2026-11-20):

| Source and exports | Amended | FDA edits | `openfda` only | Cosmetic only | List size (gzip) |
|---|---|---|---|---|---|
| 510(k), Nov 9 → 18 (176,625 → 176,683 records, 58 added) | 171,357 | 3 | 76,867 | 94,487 | 0.52 MB |
| Recall, Nov 13 → 18 | 19,706 | 1 | 0 | 19,705 | 0.06 MB |
| Enforcement, Nov 17 → 19 (new file, same records) | 0 | 0 | 0 | 0 | none stored |

- **510(k):** the 3 FDA edits are `advisory_committee` and its description on DEN250054 and DEN260027, plus `address_2`, `contact` and `product_code` on K260945. More than half of the `openfda` changes (94,487) are reorders only.
- **Recall:** every one of the 19,687 `openfda` changes is a reorder.
- **Cost:** classification adds about 28% to the second pass over amended records: 24.7 s against 19.3 s for the same 20,000 510(k) keys. Most of each pass is JSON parsing and decompression. The full 510(k) diff took 509 to 576 s on a laptop running other jobs, so it isn't comparable to ADR-018's 147 s. The weekly ingest step on GitHub took about 3 minutes of its 60-minute timeout on 2026-11-18. Peak RSS is 1.3 GB, the same as before.

## Options Considered
1. **Classify at diff time and record cosmetic fields in the change list (schema v4).**
2. **Classify at report time:** re-read both stored exports for every listed key whenever the report runs.
3. **Normalize before diffing,** so cosmetic changes are not amendments at all.

## Tradeoffs
| Criterion | 1: diff time | 2: report time | 3: normalize first |
|---|---|---|---|
| Exact change record (ADR-018) kept | Yes | Yes | No: cosmetic changes vanish |
| Each week's record is self-contained | Yes | No: needs both exports, about 0.6 GB per source | Yes |
| Contract change | Schema v4, one optional key per line | None | Changes what "amended" means |
| Extra cost per run | One more digest per field of amended records | Two export downloads per source per report | None |

## Consequences
- **Core contract:**
  - `KeyChange` gains `cosmetic` (a subset of `fields`) and `substantive_fields`.
  - A change-list line carries `"cosmetic"` only when some field is cosmetic.
  - `SCHEMA_VERSION` is 4 and `COSMETIC_SINCE` is 4. v1 to v3 manifests still load.
- **What counts as cosmetic:** text whose whitespace runs, collapsed to one space and trimmed at both ends, are equal; lists equal as multisets, so a duplicated or dropped entry is substantive; the same rules inside nested objects such as `openfda`.
  - **Case changes, a field appearing or disappearing, and any other edit are substantive.** Classifying conservatively means a real change is never hidden as cosmetic.
  - **No list in these sources has meaningful order.** If a future source has one, cosmetic must be redefined for it.
- **Classification by manifest version:** a v3 list never marked cosmetic fields, so the report shows its rows as unclassified. In a v3 list, a whitespace-only FDA field counts as an FDA edit, and the row says so.
- **Report:**
  - It only reads the store, and verifies every change list against its manifest hash.
  - It sorts runs by start time, not run id, and rejects a malformed manifest instead of skipping a week.
  - It labels each source's history as point-in-time from its first snapshot. Anything earlier is reconstructed (#91, criterion 4).
- **Removals:** a removed record stays in the earlier write-once snapshots (ADR-010), and the report names the run that first missed it. A key that returns and is removed again counts as a new removal.
- **The rates themselves** need 8 classified weekly diffs, which ends with the 2027-01-13 run (#167).
- Not changed: how records are keyed or matched (#40), the change-list key layout, counts in `SourceDiff`, raw objects.

## Definitions Altered
- `CONTEXT.md`: **run manifest** (schema v4), **change list**, and the new terms **cosmetic change** and **FDA edit**.
- Bias register: R01, R03 and R18 controls name the report; their rates move to #167.
- Sign-off: the developer accepted this design on 2026-11-20. The data science role is unassigned, so its review is pending (AGENTS.md §13).
