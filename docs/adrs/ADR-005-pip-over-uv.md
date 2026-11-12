# ADR-005: Use pip editable installs, not uv, for now

**Date:** 2026-11-11
**Status:** Accepted
**Author:** Steven Rocca

## Decision
The workspace installs with `pip install -e packages/core -e packages/pipeline` (via `make install` / `make install-dev`). uv is not adopted, and its mention is removed from `ARCHITECTURE.txt`.

## Context
`ARCHITECTURE.txt` said the root `pyproject.toml` configured uv, but nothing used it. `Makefile`, `README.md`, and CI use pip, uv is not installed on the development machine, and there is no `uv.lock`.

## Options Considered
1. **pip + editable installs** (current behavior).
2. **uv workspace** with a committed `uv.lock`.

## Tradeoffs
| Criterion | 1: pip | 2: uv |
|---|---|---|
| Works today with no changes | Yes | No; needs workspace config, lockfile, CI changes |
| Reproducible installs | Version ranges only | Exact lockfile |
| Install speed | Slower | Much faster |
| Tooling the team must install | None beyond Python | uv |

## Consequences
- `ARCHITECTURE.txt` no longer mentions uv.
- Revisit when a public dataset release needs exactly reproducible environments, or install time slows CI. Option 2 becomes a superseding ADR.
- Does NOT change any dependency or install command.

## Definitions Altered
- None.
