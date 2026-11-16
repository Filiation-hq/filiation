# ADR-012: Key RecallRecord on openFDA's product_res_number and make classification optional

**Date:** 2026-11-16
**Status:** Proposed (Alden Reagins must sign off; `packages/core/schemas/` is shared)
**Author:** Steven Rocca

## Decision
`RecallRecord` is keyed on `product_res_number` (one recalled product) and keeps `res_event_number` (the recall event), replacing `recall_number`. `classification` becomes optional, because openFDA device/recall does not carry it; #129 fills it from device/enforcement.

## Context
`RecallRecord` required `recall_number` and `classification`. Checked against the openFDA API on 2026-11-16:

- device/recall has no `recall_number`. Its product-level identifier is `product_res_number` (e.g. `Z-0001-04`), present on every record and never repeated (#77).
- device/recall has no `classification`: `_missing_:classification` matches all 59,705 records. The recall class is only in device/enforcement (40,481 records), whose `recall_number` equals `product_res_number` and whose `event_id` equals `res_event_number`.
- Real records carry about 20 fields the contract does not hold (`cfres_id`, `code_info`, `recalling_firm`, ...), so `RecallRecord.from_dict(record)` raised `TypeError` on real data.

`OpenFDAClient.fetch_recalls` hid this by filling `classification` from `product_res_number` and reading `recall_status` from a `status` field that does not exist.

## Options Considered
1. **Key on `product_res_number`, optional `classification`** — parse device/recall as it is; fill the class by joining enforcement later (#129).
2. **Join enforcement inside #77** — `classification` stays required; recalls with no enforcement record (about 19k) cannot be represented.
3. **Drop `classification` from `RecallRecord`** — a separate enforcement record type holds the class; every consumer joins two types.

## Tradeoffs
| Criterion | 1: optional class | 2: join now | 3: drop class |
|---|---|---|---|
| Parses every real recall record | Yes | No (about 19k have no class) | Yes |
| Size of this change | Small | Adds an ingest source and manifest entry | Small, but every consumer changes again later |
| Class available to consumers | After #129 | Now | Only through a second type |
| Silent wrong class | None: unknown stays `None` | None | None |

## Consequences
- `RecallRecord` fields: `product_res_number` and `res_event_number` are required and non-blank; `classification` is `str | None`. `RecallRecord.from_openfda(record)` parses a raw device/recall record, ignoring fields the contract does not carry.
- DuckDB DDL: `recalls` and `device_recall_links` key on `product_res_number`; `classification` is nullable.
- `PredicateRecallLink.recall_number` becomes `product_res_number`; `recall_classification` may be `None`.
- Does not change: event-level deduplication, the availability date, or the recall label (#93), and no derived recall data exists yet, so nothing stored needs migrating.

## Definitions Altered
- `product_res_number`, `res_event_number`, `cfres_id` and the source of `classification` are defined in `CONTEXT.md`; `recall_number` now refers only to the device/enforcement field.
