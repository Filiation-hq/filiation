# ADR-019: Rename the project from Recension to Filiation

**Date:** 2026-11-19
**Status:** Accepted
**Author:** Steven Rocca

## Decision
The project is renamed **Filiation** everywhere. This covers the name people read, the Python namespace and distributions, the CLIs, environment variables, CI, the R2 bucket, the public mirror and the GitHub repository. It supersedes ADR-011 and follows that ADR's order: code, CI and docs first; then the bucket; then the repositories.

## Context
An AI-for-biology lab already publishes as **Recension**. It owns `recension.ai` and posted *FoldingBench: Can LLMs fold proteins?* on 2026-09-18. This project took the name on 2026-11-15 (ADR-011), so they were first.

Their audience overlaps ours: biomedical ML researchers and health-tech readers. They publish benchmarks, and a public recall-prediction benchmark is our Stage 6. The name also fails the reason ADR-011 chose it, which was findability in search and AI answers. Only a few outreach messages had gone out under the name, and nothing was launched, so renaming now is the cheapest it will ever be.

In textual criticism, *filiation* is the line of descent between manuscripts: which copy was made from which. That is what this project traces for 510(k) devices, so the reason ADR-011 gave for the name still holds.

Checked on 2026-11-19:
- **Other users of the name:** the only product named Filiation is a family-tree game, a different market. No company sites use the name.
- **Domain:** `filiation.org` is bought; `filiation.com` has been registered since 2000.
- **PyPI:** `filiation-core`, `filiation-pipeline` and `filiation-models` are free.
- **GitHub:** the `filiation` organization is taken (created 2017, 8 public repositories). `filiation-hq` is free.

## Options Considered
1. **Filiation**, renamed everywhere in ADR-011's order.
2. **Keep Recension with a qualifier** ("Recension 510(k)").
3. **Collation.** In textual criticism this is the comparison of witnesses. It is a common word, so it would be hard to find in search.

## Tradeoffs
| Criterion | Filiation | Keep Recension | Collation |
|---|---|---|---|
| Conflict with an existing AI or biomedical brand | None found | Same name, earlier use, overlapping audience | Not checked; a common word |
| Findability | Rare outside genealogy | Shared with recension.ai | Poor |
| Cost now | One PR, a bucket copy, two repository renames | None | Same as Filiation |

## Consequences
- **Code and packages.**
  - The Python namespace `recension.*` becomes `filiation.*` (`packages/*/src/filiation/`).
  - The distributions become `filiation-core`, `filiation-pipeline`, `filiation-models` and `filiation-workspace`.
  - The CLIs become `filiation-pipeline` and `filiation-data`, and `RecensionDataLoader` becomes `FiliationDataLoader`.
  - Every developer runs `make install` again.
- **Environment variables.** `RECENSION_RAW_STORE`, `RECENSION_R2_ENDPOINT` and `RECENSION_R2_BUCKET` become `FILIATION_*`. Any `RECENSION_*` or `STEMMA_*` variable is refused with an error naming its replacement, so a stale `.env` or CI setting never silently falls back to the local store.
- **Bucket.** The default bucket becomes `filiation-raw`. Moving to it is a copy that checks every object's SHA-256 against its manifest, because the store is write-once (ADR-010). `recension-raw` is deleted only after the first successful run against the new bucket. Merge this change only once `filiation-raw` exists and holds the copy.
- **User agent.** The crawlers identify themselves to FDA and openFDA as `Filiation/0.1.0`.
- **Public mirror.** It moves to `filiation-hq/filiation`. The mirror allowlist keeps the `recension/` and `stemma/` paths so file history stays connected.
- **GitHub.** The private repository becomes `stevenrocca3/filiation`. GitHub redirects old URLs.
- **Unchanged:** data layout, key format, schemas and every `schema_version`. Historical text also keeps its old names: ADRs 001–018, `reports/`, `research_notes/` and `RESUME.md` (which is append-only).

## Definitions Altered
- **Filiation:** the project name, replacing Recension. `CONTEXT.md` explains the name.
- ADR-002's canonical tree is now `packages/*/src/filiation/`. ADR-010's bucket is `filiation-raw`, and its environment variables are `FILIATION_*`.
- Sign-off: the developer decided this on 2026-11-19. The data science role is unassigned, so its review is pending (AGENTS.md §13).
