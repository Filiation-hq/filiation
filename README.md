# Recension

> **An open, versioned FDA 510(k) predicate graph with per-link source citations and confidence, plus an explainable recall-risk score.**

This is the **public mirror** of Recension. A new version is published here with each release. Development happens in a private repository, and this repository is generated from it. That's why every release rewrites this repository's history, and why pull requests aren't merged here directly (see [Contributing](#contributing)).

## What's here

| Path | What it is |
|---|---|
| `packages/core/` | Data contracts (predicate edges, device nodes, recalls, run manifests), the write-once raw snapshot store, and hashing and path utilities |
| `packages/pipeline/` | openFDA bulk ingestion: downloads the 510(k), recall, and enforcement exports, stores each one write-once, diffs it against the previous snapshot, and records the run in a manifest. Also the `recension-pipeline` CLI |
| `packages/models/` | Placeholder for the recall-risk model package |
| `docs/adrs/` | Architecture decision records: what was decided, and why |
| `CONTEXT.md` | Domain glossary (510(k), predicate, recall class, availability date, and so on) |
| `ARCHITECTURE.txt` | Map of the full project. It also lists modules that aren't published here |

**Not in this repository:** PDF crawling, predicate extraction, OCR, recall linkage, and modeling. Their outputs are planned as versioned dataset releases on Zenodo. No dataset has been released yet.

## Quickstart

Requires Python 3.10+.

```bash
git clone https://github.com/recension-hq/recension.git
cd recension
make install-dev   # core, pipeline (with the pdf extra), models placeholder, pytest, ruff
make test
make lint

# Store the current openFDA 510(k), recall, and enforcement exports write-once under data/raw/
recension-pipeline ingest-openfda

# Or fetch a small API sample for ad-hoc work
recension-pipeline fetch-openfda --limit 100

# Check local data store health
recension-pipeline status
```

## Licensing

- **Code** is licensed under [AGPL-3.0-only](LICENSE).
- **Data** is licensed under [CC BY-NC 4.0](LICENSE-DATA). It is free for research, journalism, teaching, and personal projects. Commercial use needs a separate license.

[`LICENSING.md`](LICENSING.md) explains both licenses, what they don't cover, and how to get a commercial license.

## How to cite

Use [`CITATION.cff`](CITATION.cff); GitHub's "Cite this repository" button reads it. For a dataset release, cite the version DOI of the snapshot you used, as described in [`LICENSING.md`](LICENSING.md#how-to-cite).

## Contributing

Issues are welcome: bug reports, data problems you've spotted in openFDA, and questions.

Pull requests are welcome too. They are reviewed here, then applied to the private repository with credit to you, and arrive in this repository with the next release. Because Recension may also be offered under commercial licenses, outside contributions need a signed contributor license agreement (CLA) before they can be applied. See [`CONTRIBUTING.md`](CONTRIBUTING.md).

Nothing in Recension is a finding about the safety of any device.
