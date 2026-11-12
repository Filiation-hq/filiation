# ADR-003: Remove the undeclared pypdf fallback from SummaryParser

**Date:** 2026-11-11
**Status:** Accepted
**Author:** Steven Rocca

## Decision
`SummaryParser.extract_text_by_pages` no longer tries `pypdf`. pdfplumber stays the only Python PDF text extractor; the `pdftotext` system binary and the raw-stream scan remain as fallbacks.

## Context
`parser.py` imported `pypdf` as a second fallback, but no `pyproject.toml` declared it. The branch ran only when `import pdfplumber` raised `ImportError`. pdfplumber is a declared dependency of `stemma-pipeline`, so on any real install the branch was dead code. On an environment where pypdf happened to be installed, extraction results depended on undeclared state.

## Options Considered
1. **Remove the pypdf branch.**
2. **Declare `pypdf` in `packages/pipeline/pyproject.toml`** and keep the branch.
3. **Declare `pypdf` and trigger it on pdfplumber *parse* failures**, not just `ImportError`.

## Tradeoffs
| Criterion | 1: Remove | 2: Declare as-is | 3: Declare + rewire |
|---|---|---|---|
| Dead code | None | Still dead (pdfplumber always importable) | Live |
| Dependency weight | Unchanged | +1 dependency | +1 dependency |
| Behavior on corrupt PDFs | Unchanged | Unchanged | Possibly better, unmeasured |
| Scope | One deletion | One line | Behavior change that needs a benchmark |

Option 3 might help Era 1–2 PDFs, but there's no evidence yet. It belongs with the fallback-chain issue (#21), measured against the gold set.

## Consequences
- The pypdf branch is deleted. `tests/unit/test_parser_fallbacks.py` asserts it is never used.
- `docs/TECH_STACK.md` no longer lists pypdf.
- Does NOT change pdfplumber, pdftotext, or raw-stream behavior.

## Definitions Altered
- None.
