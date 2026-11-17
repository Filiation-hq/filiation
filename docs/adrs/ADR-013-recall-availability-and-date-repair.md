# ADR-013: Define a recall's availability date and repair only cross-confirmed dates

**Date:** 2026-11-17
**Status:** Proposed (decided by Steven Rocca; DS sign-off pending while the role is unassigned). Revised 2026-11-17 before implementation merged: see "Revision 1".
**Author:** Steven Rocca

## Decision
1. **Availability date (#93).** A recall becomes "known" on its device/recall posting date, which openFDA publishes as `event_date_posted` or, for 13,575 records, `event_date_created`. A posting date entered more than 30 days after the recall was terminated is a back-fill and is invalid. If the posting date is missing or invalid, the fallback is the **later** of enforcement `report_date` and `center_classification_date`, taken over the valid candidates only. Every recall records which field supplied its date. If no candidate is valid, the availability date stays empty. It is never filled from the initiation date.
2. **Bad dates (#92).** Raw data is never changed. In derived data:
   - a century-truncated initiation date is repaired only when enforcement confirms it;
   - any other date conflict, and every recall posted before it was initiated, is kept, flagged, and excluded from time-to-event calculations.

## Context
Measured on the 2026-11-16 exports (59,705 recalls; 40,532 matched to enforcement), details in #92 and #93:

- **Posting dates are missing for 13,575 recalls (23%)**, 50–85% per year for recalls initiated 2015–2021. Enforcement has `report_date` and `center_classification_date` for 13,495 of them (99.4%).
- **How the enforcement dates compare with the posting date**, where both exist:

  | Enforcement field | Same day | Later | Earlier |
  |---|---|---|---|
  | `center_classification_date` | 86% | 6% | 8.8% (median 1 day, max 591) |
  | `report_date` | <0.1% | 98.9% (median 8 days) | 1.1% (median 6 days, max 579) |

- **Posting comes a median of 52 days after initiation** (p10 25, p90 210). Using the initiation date as the availability date would leak about two months of future knowledge into every backtest.
- **Initiation dates agree across the two exports for 99.951% of matched recalls.** The exceptions:
  - 17 have a truncated century (`0013-03-05` vs `20130305`);
  - 3 differ by years (Z-0660-2013, Z-0139-2014, Z-0002-2014).
- **29 recalls in 17 events are posted before they were initiated**, by a median of 10 days and up to 550.

## Revision 1 (2026-11-17, during #142)
Profiling the 80 recalls left without a date showed that the premise above was wrong:

- **The 13,575 "missing" posting dates are not missing.** Each of them has `event_date_created` instead, and no record has both fields or neither. `created` matches enforcement's classification date 92% of the time (posted: 86%), is never earlier than it, and lags initiation by a median of 55 days (posted: 52). It is the same date under another name. The enforcement fallback had been dating these recalls a median of 8 days late.
- **Some entry dates are back-fills.** Single days hold hundreds of records: 693 posted on 2004-07-20, 333 on 2012-01-11, and 89 created on 2015-06-25/26. 376 entry dates fall after the recall's own termination. Outside those batch days, 82 of 99 trail termination by 7 days or less (fast recalls closed just before posting), and 89 by 30 or less. On batch days most trail it by months or years.

Changes:
- `event_date_created` is read as the posting date when `event_date_posted` is absent. Its source is `recall.event_date_created`, and it gets the same validity checks and the same `posted_before_initiated` flag.
- An entry date more than 30 days after a valid `event_date_terminated` is rejected as `after_termination`. A missing or invalid termination date skips this check. 224 dates are rejected: 214 on batch days, plus 10 that are years late.
- Back-filled recalls without an enforcement record keep **no** availability date until cfRES (#138) supplies the original. The alternative, the back-fill date as a flagged upper bound, was rejected: it is years late and would look like a real date. On the 2026-11-16 exports this leaves 181 recalls (0.3%) undated: 77 created and 104 posted back-fills, all initiated 2001–2011.
- The weekly report lists the five busiest raw entry days, so a new back-fill is visible even when its dates are rejected.

Result on the 2026-11-16 exports: 46,025 posted, 13,456 created, 43 enforcement `report_date`, 181 none.

## Options Considered
**Availability date:**
1. **A: Posted only.** Recalls without a posting date are excluded.
2. **B: Posted, else `center_classification_date`.**
3. **C: Posted, else the later of `report_date` and `center_classification_date`.** This is the chosen option.
4. **D: `report_date` for every recall.**
5. **E: Initiation date.**

**Bad dates:**
1. **Repair what a second source confirms, flag the rest, and exclude flagged dates from durations.** This is the chosen option.
2. **Flag everything, repair nothing.**
3. **Drop every record with a bad date.**

## Tradeoffs
| Criterion | A | B | C (chosen) | D | E |
|---|---|---|---|---|---|
| Recalls with a date | 77% | ~99.9% | ~99.9% | 2012 on only | 100% |
| Fallback earlier than the true posting date | n/a | 8.8% of comparable cases | ≤1.1% (only where both enforcement dates are early) | 1.1% | always, by ~52 days |
| Bias | Removes half of 2016–2021 | Low | Low; a few days late on average | Breaks at 2012 | Look-ahead leak |

| Criterion | Repair confirmed + flag (chosen) | Flag all | Drop all |
|---|---|---|---|
| Impossible durations reach the model | No | Yes | No |
| Records silently lost | No | No | 49 |
| Repairs rest on a second source | Yes (17) | n/a | n/a |

Being a few days late only shifts a recall's label slightly later, which is the safe direction. Being early is a leak, so the max is the right choice.

## Rules
**A candidate date is valid only if all of these hold:**
- It parses (ISO `YYYY-MM-DD` in device/recall, `YYYYMMDD` in enforcement).
- It is on or after 1976-05-28, the Medical Device Amendments.
- **It is not after the `export_date` of the snapshot it came from.** A future date is a typo by definition.
- For fallback candidates: it is not before the recall's initiation date, when that initiation date is trusted (not flagged).
- For posting (entry) dates: it is not more than 30 days after the recall's valid termination date.

Invalid candidates are dropped *before* the max and counted by reason: `unparseable`, `before_mda`, `future_dated`, `before_initiation`, `after_termination`.

**Resolving the availability date:**
1. If the posting date (`event_date_posted`, else `event_date_created`) is valid, use it. The source is whichever field held it.
2. Otherwise, take the max over the **valid, non-null** subset of {`report_date`, `center_classification_date`}:
   - The source is whichever field won. A tie goes to `report_date`.
   - Null and invalid candidates are never compared. One valid candidate wins on its own.
3. Otherwise the availability date is `None` with source `none`. The recall is counted, and it is not used as a dated label event.

The max must run after validation. If it ran first, one future-dated typo (say `20310305` for `20130305`) would win and silently push availability years late. A posted date that is itself future-dated or otherwise invalid is treated as missing, so step 2 applies; its rejection reason is recorded.

**Repairing initiation dates:**
- A device/recall initiation year below 100 is repaired only when enforcement `recall_initiation_date` has the same month and day and the same last two digits of the year. The repaired date takes enforcement's value, with source `enforcement.recall_initiation_date` and flag `century_repaired`.
- These are kept, flagged, and excluded from time-to-event calculations:
  - any other disagreement with enforcement (`initiation_conflict`);
  - an initiation date that fails the validity rules above (`initiation_invalid`);
  - a valid posted date before a trusted initiation date (`posted_before_initiated`).

All counts are reported per run in the field-completeness report (#92).

## Consequences
- **What changes:**
  - The derived recall record gains `availability_date`, `availability_source`, `initiation_date_source`, and a list of date flags. That is a contract change in `packages/core/schemas/`, built in #142.
  - Label construction (#93) reads `availability_date`, never `event_date_posted` directly.
  - The weekly report counts recalls by availability source and flag.
- **What stays the same:**
  - Raw objects in the store stay byte-for-byte as downloaded (ADR-010).
  - `classification` and the enforcement join keep the behavior from #129.
- **Known limits:**
  - A plausible-looking typo that stays inside the valid range (e.g. 2003 written for 2013) can't be caught by these rules. Neither can a recall initiated before 2012, which has no enforcement record to cross-check against. cfRES (#138) is the planned second source for both.
  - Where both enforcement dates are earlier than the true posting date (at most 1.1% of fallbacks), the fallback is still early.

## Definitions Altered
- **Availability date** (new) is the date a recall is treated as publicly known for labels and backtests. It is resolved by the rules above. `CONTEXT.md` is updated to match.
- `event_date_posted` is no longer read directly by label code.
