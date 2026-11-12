# ADR-007: Survival analysis library for packages/models

**Date:** 2026-11-11
**Status:** Proposed (drafted by DE for DS review; Alden Reagins owns the decision)
**Author:** Steven Rocca (draft), Alden Reagins (decision owner)

## Decision (proposed)
Use **lifelines** for the baseline Cox PH and time-varying exposure models, with cluster-robust standard errors by product code. Treat true product-code **frailty** as a separate, later decision: only if the baseline shows clustering that robust errors can't handle, evaluate R `coxme` (via `rpy2` or a separate R step) in a superseding ADR.

## Context
`ARCHITECTURE.txt` plans Cox PH models, product-code frailty, time-varying exposure matrices (immortal-time-bias safe), and temporal backtests with calibration. `packages/models` declares no dependencies yet (#10). The choice affects feature formats, backtesting code, and whether the stack stays pure Python.

## Options Considered
1. **lifelines.** Python Cox PH, time-varying Cox, cluster-robust variance, calibration helpers.
2. **scikit-survival.** Python, scikit-learn API, Cox PH plus ML survival models.
3. **statsmodels `PHReg`.** Python Cox PH with strata.
4. **R `survival` / `coxme`** via `rpy2`. Mature frailty and mixed-effects Cox.

## Tradeoffs
Capabilities below are **from the drafter's knowledge and not verified in this repo. Check each one against current library docs before accepting.**

| Criterion | lifelines | scikit-survival | statsmodels PHReg | R coxme |
|---|---|---|---|---|
| Time-varying covariates (start/stop) | Yes (`CoxTimeVaryingFitter`) | No (to verify) | To verify | Yes |
| Frailty / random effects by product code | No; cluster-robust SE only | No | No (to verify) | Yes |
| Calibration / backtest tooling | Some built in | sklearn-style metrics | Minimal | Separate packages |
| Pure Python (simple CI, one runtime) | Yes | Yes | Yes | No; needs an R toolchain |
| Fit with pandas DataFrames from `data/derived/` | Direct | Needs array conversion | Direct | Needs conversion |

## Consequences (if accepted)
- `packages/models/pyproject.toml` declares `lifelines`, and `tests/unit/test_dependencies.py` stays green once code imports it.
- Feature builders emit start/stop long-format tables (what time-varying Cox needs), which also helps avoid immortal-time bias.
- Frailty stays out of scope until a superseding ADR adopts it.
- Does NOT change any `packages/core` schema.

## Definitions Altered
- None until accepted. If accepted, `CONTEXT.md` should define "frailty" vs. "cluster-robust" as used in the risk score.

## Acceptance checklist (DS)
- [ ] Verify each "to verify" cell above against current docs
- [ ] Confirm whether product-code frailty is required for the first risk score, or robust SE is enough
- [ ] Set Status to Accepted, or supersede with an alternative
