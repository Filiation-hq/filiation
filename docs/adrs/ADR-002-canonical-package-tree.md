# ADR-002: packages/*/src/stemma/ is the only source tree

**Date:** 2026-11-11
**Status:** Accepted (namespace renamed to `recension` by ADR-011)
**Author:** Steven Rocca

## Decision
Source code lives only in `packages/core/src/stemma/core/` and `packages/pipeline/src/stemma/pipeline/` (and later `packages/models/src/stemma/models/`). The duplicate top-level `stemma/` tree is deleted.

## Context
The repo had two copies of the same modules: `stemma/` at the root and `packages/*/src/stemma/`. Only the `__init__.py` files differed. The root `pyproject.toml` (`package-dir = {"" = "."}`) pointed at the root copy, while `Makefile` and both package `pyproject.toml` files pointed at `packages/`. Two copies drift, and fixes land in one but not the other (e.g. #14 exists in both).

The `packages/` copy lacked `__init__.py` in every subpackage, so `setuptools.find_packages` would have left `schemas/`, `utils/`, `crawler/`, `extraction/`, `linkage/`, and `ocr/` out of a built wheel.

## Options Considered
1. **Keep `packages/*/src/stemma/`, delete root `stemma/`.** Matches `ARCHITECTURE.txt`, `README.md`, `Makefile`, and the per-package build config.
2. **Keep root `stemma/`, delete `packages/`.** A single flat package, which would collapse the DE/DS package boundaries.

## Tradeoffs
| Criterion | Option 1: packages/ | Option 2: root stemma/ |
|---|---|---|
| Matches documented architecture | Yes | No; docs rewrite needed |
| DE/DS ownership boundaries | Separate installable packages | One package, shared ownership |
| Independent dependency sets | Yes (core light, pipeline heavier) | No |
| Work needed | Add 6 empty `__init__.py` files | Rewrite build config and docs |

## Consequences
- Root `stemma/` removed. Root `pyproject.toml` declares `packages = []`, so it holds only workspace and tool config and ships no code.
- Every subpackage under `packages/*/src/stemma/<pkg>/` has an `__init__.py`. `packages/*/src/stemma/` itself has none, so it stays an implicit namespace shared by core, pipeline, and models.
- `tests/unit/test_package_layout.py` pins all three rules.
- Does NOT change any module contents, imports, or public APIs.

## Definitions Altered
- **Source tree:** now means `packages/*/src/stemma/` only.
