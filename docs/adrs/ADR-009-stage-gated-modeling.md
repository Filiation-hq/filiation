# ADR-009: Gate modeling on data-quality exit criteria

**Date:** 2026-11-11
**Status:** Proposed (accepted in conversation by Steven Rocca; requires Alden Reagins agreement, since it changes DS scope)
**Author:** Steven Rocca

## Decision
No model training or modeling code (survival models, features, backtests) until the Stage 2a exit criteria in `docs/ROADMAP.md` are met. Before then, data science work is analysis only.

## Context
A survival model trained on a predicate graph with unmeasured extraction error produces confident but wrong risk scores. The gold set, per-era accuracy numbers, and descriptive graph statistics determine whether modeling is viable and which eras are trustworthy, so they must come first.

## Options Considered
1. **Gate on exit criteria**: modeling begins when Stage 2a's per-era precision and recall thresholds are met on a frozen snapshot.
2. **Gate on calendar**: modeling begins on a fixed date.
3. **Parallel tracks**: modeling starts now against provisional data.

## Tradeoffs
| Criterion | Exit criteria | Calendar | Parallel |
|---|---|---|---|
| Model trained on measured data | Yes | Not guaranteed | No |
| Rework risk | Low | Medium | High (features built on unstable contracts) |
| Schedule predictability | Lower | High | High |
| DS focus | Analysis that feeds the model | Splits near the date | Split from the start |

## Consequences
- ADR-007 (survival library) stays Proposed and is deferred to Stage 2b. Issue #9 moves to the Stage 2b milestone.
- `packages/models` stays an empty installable scaffold; `02_survival_baseline` and `03_calibration_check` stay placeholders.
- `01_jama_replication` (a descriptive risk-ratio comparison) belongs in Stage 2a.
- If some eras miss their threshold, Stage 2b may begin on the passing eras only, named explicitly in the 2b milestone description.

## Definitions Altered
- **Stage**: a roadmap phase with testable exit criteria (`docs/ROADMAP.md`). Add to `CONTEXT.md`.
