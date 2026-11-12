# ADR-006: Reserve LLM_FALLBACK; choose no LLM provider until the pilot shows a recall gap

**Date:** 2026-11-11
**Status:** Proposed (DE accepts; needs DS sign-off because `ExtractionMethod` is a shared contract)
**Author:** Steven Rocca

## Decision
`ExtractionMethod.LLM_FALLBACK` stays in the schema as a **reserved** value that nothing emits yet. No LLM provider, SDK, or code is added. An LLM fallback is adopted only if the 500-PDF pilot shows regex + OCR recall below the per-era target that DS sets in the gold-set design. The provider is then chosen in a new ADR with cost and accuracy measured on the gold set.

## Context
The schema and README list `LLM_FALLBACK`, and `ARCHITECTURE.txt` mentions "LLM parsers", but no LLM code or provider exists. Choosing a provider now would mean guessing at cost, accuracy, and reproducibility before any extraction benchmark exists.

## Options Considered
1. **Reserve the enum value, defer the provider** until pilot data shows a gap.
2. **Pick a provider now** and build the fallback.
3. **Remove `LLM_FALLBACK`** from the schema.

## Tradeoffs
| Criterion | 1: Reserve + defer | 2: Build now | 3: Remove |
|---|---|---|---|
| Evidence-based | Yes; waits for per-era recall | No | Pre-judges the outcome |
| Data contract stability | Unchanged | Unchanged | Breaking change to a published enum |
| Cost / API keys / reproducibility risk | None yet | Immediate | None |
| Effort now | Docs only | High | Low, but may be reverted |

## Consequences
- Nothing emits `LLM_FALLBACK` until a superseding ADR accepts a provider.
- `ARCHITECTURE.txt` and `docs/TECH_STACK.md` describe it as reserved.
- Decision point: after the pilot benchmark lands in `RESUME.md`.
- Does NOT change the schema.

## Definitions Altered
- **`LLM_FALLBACK`:** now explicitly "reserved; not emitted by any pipeline stage."
