# ADR-017: Fix the recall label to openFDA recall records, one label per device application and recall event

**Date:** 2026-11-19
**Status:** Accepted
**Author:** Steven Rocca

## Decision
The recall label comes only from FDA recall records: openFDA device/recall, with the class and fallback dates joined from device/enforcement. Part 806 reports and other safety signals may become features, but never the label.

One label, a `DeviceRecallEvent`, is one recall event (`res_event_number`) as it touches one device application. An application is either a 510(k) (`pathway` `"510k"`, keyed by K-number) or a PMA (`"pma"`, keyed by base P-number).

A label counts as known only from its availability date (ADR-013). Only 510(k) labels join the predicate graph. `recall_status` changes after posting, so it is never a label or a target.

## Context
#93 requires one label source, an event identity, and an availability date that every cutoff uses (R02, R09). ADR-012 added the event id and ADR-013 the availability date, but no rule yet said what one label is. `RecallLinker` made one link per recalled product, and dated "recalled before clearance" by the initiation date.

Measured on the 2026-11-18 exports: device/recall (59,705 products), device/enforcement (40,535), device/classification and device/pma:
- **Products repeat a device within an event.** 3,704 events name the same K-number on more than one product. Grouped per device and event, the 48,647 (K-number, product) pairs become 24,663 labels on 13,090 devices: counting products would almost double the recall count.
- **Dates are uniform within an event.** In all 6,880 multi-product events, every product shares one posting date and one initiation date.
- **Class is per product.** 6 of 15,191 enforcement events mix classes, e.g. event 73283 is Class I and Class II.
- **`recall_status` is not frozen.** openFDA says FDA does not update a recall's status after classification. The data disagrees: 44,691 products are `Terminated` and 439 `Completed`, states only reached after classification, and 14,575 are `Open, Classified`. The status is this snapshot's state, with no history.
- **20,792 products (34.8%) name no K-number.** By the pathway their product code is limited to in device/classification:

  | Pathway | Products |
  |---|---|
  | 510(k) exempt | 8,179 |
  | 510(k) | 4,863 |
  | `submission_type_id` 7 (undocumented by openFDA; kits and trays) | 3,405 |
  | PMA | 3,054 |
  | No code, unknown code, Contact ODE, other undocumented ids | 1,291 |

  - **PMA numbers.** 2,744 of these products name PMA numbers. openFDA writes them with supplement suffixes (`P850020S011`, `P050052/SO52`, `PMA-P900023`). Only 1,888 match the device/pma export as written; 2,711 match after reduction to the base P-number.
- **Adjacent sources do not supply the missing K-numbers:**
  - `openfda.k_number` lists every 510(k) under the product code. On recalls with real K-numbers it has a median of 288 entries and equals the true set in 33 of 38,296.
  - Firm plus product code alone gives a unique 510(k) that is right 1,475 of 3,974 times.
  - cfRES shows no "510(K)Number" for any of 20 sampled gap recalls. Two control recalls show theirs, matching openFDA.
- **Part 806 reports are not a public dataset.** A correction or removal reaches openFDA only when FDA classifies it as a recall. Counting other 806 reports would mix in actions that FDA never classified as recalls ([21 CFR 806](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-H/part-806)).

## Options Considered
1. **One label per (pathway, application, event).** Products of one event that name the application are combined.
2. **One label per (K-number, event).** PMA recalls are added later under a different key.
3. **One label per (K-number, product).** This keeps the current linker behaviour.
4. **One label per event.** It is shared by every device the event names.

## Tradeoffs
| Criterion | 1: pathway, application, event | 2: K-number, event | 3: K-number, product | 4: event |
|---|---|---|---|---|
| Counts one recall once per device | Yes | Yes | No: 48,647 vs 24,663 | Yes |
| PMA recalls as labels | Yes: 1,390 labels on 489 PMAs | Only after the label key changes | No | Yes, but not per device |
| Class for a device | Most severe among its products | Same | Exact per product | One class for devices with different classes |
| Joins to the predicate graph | 510(k) labels, on `application_number` | Directly | Directly | Needs a second table |

## Consequences
- **Core contract.** `recalls.py` gains `DeviceRecallEvent`, `device_recall_events`, `normalize_pma_number`, `PATHWAYS` and `CLASS_SEVERITY`. `RecallRecord` gains `pma_numbers`, holding base P-numbers; a value without one is kept as written and yields no label. The models package can build labels without importing the pipeline.
- **How a label is combined from its products:**
  - **Class:** the most severe of Class I, II and III, ignoring products without a class. It is `None` if no product has one, and an unknown class string is rejected.
  - **Availability:** the earliest product availability date. A tie goes to the source earliest in ADR-013's order. It is `"none"` when every product was resolved without a valid date, and `None` when none was resolved. If only some products were resolved, the label is rejected, because ADR-013 resolution must run first.
  - **Initiation date:** the earliest valid ISO date among the products.
  - **Design-related:** true if any product is design-related.
  - **Date flags:** all of the products' flags.
- **Labels per product.** A product naming both a K-number and a PMA gets a label on each pathway.
- **Inputs that are skipped or rejected:**
  - A product naming neither yields no label.
  - A product key seen twice with different content is rejected; an identical duplicate is collapsed.
  - An unknown pathway is rejected, and so is a PMA label not keyed on a base P-number.
- **Cutoffs.** `DeviceRecallEvent.known_by(as_of)` is true only when the availability date is on or before the cutoff. A label without an availability date is never known. Initiation never stands in.
- **Linker.** `RecallLinker` indexes only 510(k) labels and makes one `PredicateRecallLink` per event, carrying `product_res_numbers` and `recall_availability_date`. `recalled_prior_to_clearance` is true only if the recall was known the day before the child's clearance. It is `None` when either date is missing, where it used to compare initiation dates.
- **DuckDB DDL.**
  - `device_recall_links` keys on `(pathway, application_number, res_event_number)` and adds `product_res_numbers`, `availability_date`, `availability_source` and `date_flags`.
  - `recalls` adds `pma_numbers`.
  - No link data has been written yet, so nothing needs migrating.
- **`recall_status`** stays on `RecallRecord` as raw snapshot data, but it is not carried on `DeviceRecallEvent` or `device_recall_links`. `event_date_terminated` isn't either, for the same reason. Status changes across snapshots are measured by #91.
- **The 510(k) gap.** 4,639 products (1,719 events) on 510(k)-pathway codes without a PMA number get no label, so their devices look recall-free. This is recorded as R47 and measured until #161 links them with a measured precision. `openfda.k_number` must never be used as a device's K-number.
- **Other sources:**
  - cfRES (#138) may fill dates or classes for recalls already in device/recall. Adding recall events that openFDA lacks would need a new ADR.
  - Part 806 data, MAUDE, safety communications and warning letters may only be features, each with its own availability date.
- **Not changed:** ADR-013's date resolution, the recall class join (#129), and `RecallRecord`'s key. 510(k)-exempt recalls have no application to label.

## Definitions Altered
- `CONTEXT.md`: new terms **recall event** and **recall label**; `recall_status`, `pma_numbers` and `openfda.k_number` are described.
- `PredicateRecallLink`: `product_res_number` becomes `product_res_numbers`, and `recall_availability_date` is added.
- Sign-off: the developer accepted this on 2026-11-19. The data science role is unassigned, so its review is pending (AGENTS.md §13).
