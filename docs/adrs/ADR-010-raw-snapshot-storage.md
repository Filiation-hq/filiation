# ADR-010: Raw snapshot storage on Cloudflare R2

**Date:** 2026-11-12
**Status:** Proposed (decided by Steven Rocca in the #44 grilling; Alden Reagins must sign off on the run manifest contract)
**Author:** Steven Rocca

## Decision
Canonical raw snapshots live in a private Cloudflare R2 bucket, `stemma-raw`. The bucket is write-once, uses one key layout shared by local and remote, and only two writers may write to it: the scheduled GitHub Actions workflow and the backfill job on Steven's Oracle VM, both running code from `main`. Each run is described by a run manifest. Alden reads raw data through a core loader, never through bucket paths.

## Context
Stage 1 (#45) runs ingestion weekly on ephemeral GitHub-hosted runners, and raw data is git-ignored, so snapshots need durable storage outside both. Measured 2026-11-12 from `api.fda.gov/download.json`:
- device/510k: 1 partition, 238.4 MB, 176,625 records, `export_date` 2026-11-09.
- device/recall: 1 partition, 276.5 MB, 59,705 records, `export_date` 2026-11-10.

The total size of the CDRH PDF history is **unknown**. A guess of about 100k summaries at 0.2–2 MB each gives 20–200 GB; the 500-PDF pilot will replace that guess with a measurement.

## Options Considered
1. **Cloudflare R2**: S3-compatible API with no egress fees; already planned in `ARCHITECTURE.txt`.
2. **AWS S3 or GCS**: the industry default, with metered egress.
3. **Oracle Object Storage**: S3-compatible, on a vendor Steven already uses; requires compartment and IAM-policy setup.
4. **GitHub Releases or Actions artifacts**: no new account, but artifacts expire and release assets are size-capped.
5. **Oracle VM disk**: free, but a single machine with no redundancy, SSH-only access, and (believed) idle-reclamation risk on Always Free instances.

## Tradeoffs
| Criterion | R2 | S3 / GCS | Oracle Object Storage | GitHub Releases / artifacts | Oracle VM disk |
|---|---|---|---|---|---|
| Egress when DS pulls snapshots | Free | Metered | Large free allowance* | Free | Free |
| Durability | Managed | Managed | Managed | Managed, but artifacts expire | One disk |
| Scoped read-only access | API tokens | IAM | IAM policies | Repo access | SSH keys |
| Setup effort | Low | Medium | Medium | None | Low, plus upkeep |
| Fits tens of thousands of PDFs | Yes | Yes | Yes | No | Space-limited* |

\* From memory; not verified.

## Decision Details

**Writers.** The scheduled or `workflow_dispatch` workflow is a canonical writer, and so is the one-time backfill job on Steven's Oracle VM. Both must run code from `main`. The backfill exists because a full PDF crawl at 1 request/second takes about 28 hours, beyond the GitHub-hosted job limit (believed to be 6 hours). The same VM will run Stage 2a's Era 1 OCR. Local runs write to `data/raw/` using the same layout and are never uploaded. CI tests use fixtures only.

**openFDA source.** The raw artifact is the bulk export zip, stored byte-for-byte with its SHA-256 and `export_date` (#39). The API is used only for ad-hoc and sample commands. "Incremental" means diffing consecutive exports (#40).

**Key layout** (identical under `data/raw/` and in the bucket):
```
openfda/510k/export_date=<YYYY-MM-DD>/<openFDA file name>.json.zip
openfda/recall/export_date=<YYYY-MM-DD>/<openFDA file name>.json.zip
cdrh/pdfs/<K-number>/<sha256>.pdf
cdrh/missing/<K-number>/<YYYY-MM-DD>.json
runs/<YYYY-MM-DD>_<run-id>/manifest.json
latest.json
```
- JSON exports are partitioned by openFDA's `export_date`, so a week with no change creates no new object.
- PDFs are keyed by K-number and content hash, so a re-posted PDF becomes a new version and nothing is copied per run.
- A missing marker records the date a K-number's PDF returned 404 at every candidate URL (#43). The crawl state lives only in the store: a K-number with a PDF is done, and one with a marker is checked again 30 days later if decided within the last year, or 365 days later otherwise. So a crawl resumes on any machine.
- A run's manifest lists every object in the run (key, SHA-256, size, record count where applicable), plus `export_date`, commit SHA, and run ID (#42).

**Immutability.**
- Writing to a key that already exists is refused, and a test enforces this.
- `latest.json` is the only mutable object. It is updated only after the run's manifest is written, so a failed run never moves it.
- R2 enforces this too: `R2RawStore.put_once` sends `If-None-Match: *`, and R2 evaluates it atomically, answering 412 PreconditionFailed if the key exists. Verified 2026-11-12 against Cloudflare's [conditional operations docs](https://developers.cloudflare.com/r2/api/s3/conditional-operations/).
- Each upload carries a unique `write-id` in its metadata. When a timed-out upload commits and boto3's retry then gets 412, `put_once` reads the stored `write-id`; if it matches, the first attempt succeeded and the run continues. This is the pattern Cloudflare's docs recommend.

**Retention.** Keep every export, PDF version, and manifest. Review when the bucket passes 100 GB or monthly cost passes the budget. Stage 2b needs point-in-time snapshots, so old weeks are training data, not clutter.

**Read interface.**
- Alden works from derived Parquet by default.
- Raw access goes through `load_raw_snapshot(source, as_of=None)` in `stemma-core`. It resolves `latest.json` or the manifest in effect at `as_of`, caches objects under `data/raw/`, and returns paths or DataFrames.
- The manifest schema lives in `packages/core/src/stemma/core/schemas/` and is a shared contract.

**Code structure.**
- A `RawStore` interface (`put_once`, `get`, `exists`, `list`) in `stemma.core.storage.raw_store`, with `LocalRawStore` (filesystem) and `R2RawStore` (boto3). `STEMMA_RAW_STORE` (`local` by default, or `r2`) selects the implementation; `r2` also reads `STEMMA_R2_ENDPOINT` and optionally `STEMMA_R2_BUCKET`.
- One shared write-once test runs against both. `R2RawStore` tests use botocore's `Stubber`, so there is no network access.

**Dependencies.**
- `boto3>=1.35.2` (the first release whose PutObject accepts `IfNoneMatch`) is an optional `stemma-core[remote]` extra, because `R2RawStore` lives in core. The pipeline reaches R2 through core and declares no `boto3` of its own; `make install` installs `stemma-core[remote]`. (Amended 2026-11-12 in #53; the original draft also added `boto3` to `stemma-pipeline`, which ADR-004's import test would reject.)
- This ADR satisfies ADR-004's requirement that a new dependency have an ADR.

**Credentials.**
- R2 API tokens are scoped to `stemma-raw`.
- The writer token (Object Read & Write) is stored as a GitHub Actions secret on an `ingestion` environment restricted to `main`. A copy lives on the Oracle VM for the backfill, outside the repo.
- Steven and Alden each get a read-only token (Object Read), kept in a git-ignored local `.env`.
- Tokens are rotated every 90 days, and immediately if exposed.

**Cost.**
- The ceiling is $10/month, with a Cloudflare billing notification.
- The full PDF crawl is gated on the 500-PDF pilot's measured average size and the projected total.
- R2 pricing, believed to be 10 GB free and then about $0.015/GB-month with free egress, needs verifying before the ceiling is relied on.

## Consequences
- New code: `RawStore` and its two implementations, the manifest schema, and `load_raw_snapshot`.
- New infrastructure (Steven): a Cloudflare R2 bucket, three tokens, a GitHub `ingestion` environment, and the backfill job on the Oracle VM.
- #39, #40, and #42 are rescoped to bulk exports, export diffs, and write-once manifests.
- Unchanged: the 3-tier data layout, git-ignoring of raw and interim data, and existing data contracts.
- Interim data (OCR text, regex candidates) stays local; it can be regenerated from raw data plus a code version.
- Derived release storage, likely a separate `stemma-derived` bucket, is decided in Stage 2a.

## Definitions Altered
- Adds the storage terms in `CONTEXT.md` §7: raw snapshot, run manifest, `export_date`, write-once, `latest.json` pointer, `RawStore`, canonical writer.
