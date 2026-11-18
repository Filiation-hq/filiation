# ADR-014: Declare PDF extraction dependencies as an optional `pdf` extra

**Date:** 2026-11-18
**Status:** Proposed
**Author:** Steven Rocca

## Decision
`pdfplumber` moves from `recension-pipeline`'s required dependencies to an optional extra, `pdf`. `make install` and `make install-dev` install the extra. A pipeline installed without the extra logs a warning whenever the parser falls back for lack of `pdfplumber`.

## Context
The public mirror (`recension-hq/recension`) ships `packages/pipeline` without the extraction and OCR modules, which are the only code that imports `pdfplumber`. ADR-004 requires each package to declare exactly what its source imports. With `pdfplumber` required, the mirror's own dependency test fails, and every public install pulls in a PDF stack it never uses.

## Options Considered
1. **Optional `pdf` extra.** The public install stays lean, and the private install opts in through the Makefile.
2. **Keep it required, and let the dependency test tolerate packages that are missing from the mirror.** This weakens an ADR-004 guard to fit the mirror.
3. **Keep it required, and edit `pyproject.toml` while building the mirror.** The public file would then differ from the source of truth, which is easy to get wrong.

## Tradeoffs
| Criterion | Extra (chosen) | Tolerant test | Edit during build |
|---|---|---|---|
| Public install works and passes its tests | Yes | Yes | Yes |
| ADR-004 guard unchanged | Yes | No | Yes |
| One `pyproject.toml` for both repos | Yes | Yes | No |
| Risk of degraded extraction | Install without `[pdf]` falls back to `pdftotext`; made visible by a warning | None | None |

## Consequences
- The parser's `ImportError` fallback, dead code since ADR-003, is live again for installs without `[pdf]`. It now logs at WARNING and names the extra, instead of DEBUG.
- `make install`, `make install-dev`, and every workflow that uses them (CI, TDD guard, weekly ingest) install `packages/pipeline[pdf]`.
- The public `pyproject.toml` lists the `pdf` extra even though no public module imports `pdfplumber`. The test that ties the extra to its import lives with the private parser tests.
- Does NOT change the fallback order (`pdfplumber`, then `pdftotext`, then the raw-stream scan) or ADR-003's removal of `pypdf`.

## Definitions Altered
None.
