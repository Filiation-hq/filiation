# ADR-011: Rename the project from Stemma to Recension

**Date:** 2026-11-15
**Status:** Proposed (decided by Steven Rocca on 2026-11-15; Alden Reagins to sign off)
**Author:** Steven Rocca

## Decision
The project is renamed **Recension** everywhere: the name people read, the Python namespace and distributions, the CLI, environment variables, CI, the R2 bucket, and the GitHub repository. Code, CI and docs change first; the bucket and the repository follow in that order, because each depends on the one before.

## Context
In textual criticism a *stemma* is the family tree of a text's manuscripts, and a *recension* is a revised edition of a text established by comparing its witnesses. Recension keeps the same idea, a lineage of versions checked against sources, and fits a project built on versioned, write-once snapshots. "Stemma" alone also has unrelated meanings (for example the simple eyes of insect larvae), which hurts search and AI-answer visibility, as the build-in-public plan noted. Nothing has been published under the old name yet, so renaming before the public launch is cheaper than renaming after it.

## Options Considered
1. **Brand only** — rename what people read; keep `stemma.*`, `STEMMA_*` and `stemma-raw` internally.
2. **Everything, in order** — rename code, packages, env vars and CI now; create `recension-raw` and copy any objects; then rename the repository.
3. **Keep Stemma** — pair it with a qualifier ("Stemma 510(k)") everywhere.

## Tradeoffs
| Criterion | Brand only | Everything, in order | Keep Stemma |
|---|---|---|---|
| Name findability | Better | Better | Weak without a qualifier |
| Long-term confusion | Two names forever in code and docs | One name | One name |
| Cost now | Small | One PR stack, a bucket copy, a repo rename | None |
| Risk | Low | A stale `.env` or CI secret could point at the wrong store | None |

## Consequences
- Python namespace `stemma.*` becomes `recension.*` (`packages/*/src/recension/`); distributions become `recension-core`, `recension-pipeline`, `recension-models` and `recension-workspace`; the CLIs become `recension-pipeline` and `recension-data`. Every developer reruns `make install`.
- `STEMMA_RAW_STORE`, `STEMMA_R2_ENDPOINT` and `STEMMA_R2_BUCKET` become `RECENSION_*`. A setting that still uses an old name, without its new one, is refused with an error naming the new variable, so a stale `.env` can never silently fall back to the local store.
- The default bucket becomes `recension-raw`. Because the store is write-once (ADR-010), moving to it is a copy with every object's SHA-256 checked against its manifest; `stemma-raw` is deleted only after the first successful run against the new bucket (#124). The env-var change merges only once the bucket exists.
- The GitHub repository is renamed last (#125). GitHub redirects old URLs, but `AGENTS.md`, docs and local remotes are updated.
- It does NOT change any data layout, key format, schema, or `schema_version`. Historical ADR text keeps the old name; ADR-002 and ADR-010 note the parts this ADR supersedes.

## Definitions Altered
- **Recension**: the project name, replacing Stemma. `CONTEXT.md` explains the name.
- ADR-002's canonical tree is now `packages/*/src/recension/`. ADR-010's bucket is now `recension-raw`, and its env vars are `RECENSION_*`.
