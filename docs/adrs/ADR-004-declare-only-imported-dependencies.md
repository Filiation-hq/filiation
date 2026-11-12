# ADR-004: Declare only dependencies the code imports

**Date:** 2026-11-11
**Status:** Accepted
**Author:** Steven Rocca

## Decision
Each package declares exactly the third-party packages its source imports, plus documented indirect ones (`pyarrow` for pandas Parquet I/O). The root `pyproject.toml` declares no runtime dependencies. Removed: `pydantic`, `duckdb` (core); `requests`, `pillow`, `tqdm` (pipeline); `pytest-cov` (dev); and the root's duplicate runtime list.

## Context
`docs/TECH_STACK.md` found six declared-but-unused dependencies. ADR-001 already chose dataclasses over pydantic, HTTP uses stdlib `urllib`, and DuckDB appears only in DDL strings. The root `pyproject.toml` duplicated the packages' lists even though it ships no code (ADR-002).

## Options Considered
1. **Remove unused dependencies now, and re-add each one in the PR that first imports it.**
2. **Keep the planned ones (`duckdb`, `pillow`) declared ahead of use.**
3. **Leave everything as is.**

## Tradeoffs
| Criterion | 1: Remove now | 2: Keep planned | 3: As is |
|---|---|---|---|
| Install size / attack surface | Smallest | Medium | Largest |
| Declared deps = real needs | Yes, enforced by test | Partly | No |
| Work to re-adopt later | One line + ADR | None | None |
| Signal to readers | Accurate | Mixed with plans | Misleading |

## Consequences
- `tests/unit/test_dependencies.py` fails if a package declares something it doesn't import, or if the root declares runtime deps. New indirect dependencies must be listed in its `INDIRECT` map with a reason.
- Verified: a fresh venv with only `packages/core`, `packages/pipeline`, and pytest passes the full suite. `pillow` still arrives transitively through pdfplumber.
- Re-adopting DuckDB for the analytical store or Pillow for OCR preprocessing needs its own ADR.
- Known gap, not changed here: `stemma-pipeline` imports `stemma.core` but cannot declare `stemma-core` as a local path dependency with plain setuptools. `make install` installs both.

## Definitions Altered
- None.
