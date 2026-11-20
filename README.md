# Filiation

Filiation is a project for tracing FDA 510(k) predicate lineage and checking what public filings report about testing for device changes.

This [public mirror](https://github.com/filiation-hq/filiation) ships the openFDA ingestion CLI, its supporting data contracts and snapshot storage, and tests. Today you can fetch a small sample of 510(k) records or build local snapshots from the bulk exports. The [roadmap](docs/ROADMAP.md) describes the planned predicate graph and versioned dataset releases.

The mirror is scheduled to update every Monday from the private development repository. Changes to the set of published paths rebuild its history.

## Quickstart

Requires Python 3.10+, internet access to openFDA, and permission to write under the clone's `data/raw/` directory.

```bash
git clone https://github.com/filiation-hq/filiation.git
cd filiation
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e packages/core -e packages/pipeline
filiation-pipeline fetch-openfda --limit 100
```

The command requests up to 100 510(k) clearance records and writes a JSON array to `data/raw/openfda/sample_510k.json`. It uses the public API without an API key or cloud credentials. A rerun replaces the sample file. Use `--output` to choose another writable path.

### Optional bulk ingestion

Run `filiation-pipeline ingest-openfda` to download the full 510(k), recall, and enforcement exports, store them under `data/raw/` by default, and record a run manifest. Allow disk space for the exports and retained snapshots. The 510(k) export alone can inflate to 1.6 GB, and a full 510(k) snapshot comparison has used about 1.3 GB peak RAM. See the [storage design](docs/adrs/ADR-010-raw-snapshot-storage.md) and [snapshot comparison notes](docs/adrs/ADR-021-cosmetic-changes-in-change-lists.md).

## Manual evidence example

In the FDA-hosted K233783 summary, applicant PECA Labs identifies [K221628 as the predicate](https://www.accessdata.fda.gov/cdrh_docs/pdf23/K233783.pdf#page=5) in Section VI.

| Configuration | What the applicant reported | Source |
|---|---|---|
| Unchanged Carbon | The submission carried forward performance data from K221628 for these configurations. | [K233783, PDF page 5, Section VII](https://www.accessdata.fda.gov/cdrh_docs/pdf23/K233783.pdf#page=5) |
| Non-Carbon | The applicant included additional performance data in the submission and its appendices. | [K233783, PDF page 5, Section VII](https://www.accessdata.fda.gov/cdrh_docs/pdf23/K233783.pdf#page=5). The test list continues on [PDF page 6](https://www.accessdata.fda.gov/cdrh_docs/pdf23/K233783.pdf#page=6). |

Project example by Steven Rocca. Source checked November 20, 2026. This manual example applies to the configurations in this filing. Filiation is independent of the FDA.

## Licensing

- Code is licensed under [AGPL-3.0-only](LICENSE).
- [CC BY-NC 4.0](LICENSE-DATA) applies to the extent Filiation holds rights in a released dataset. openFDA data is generally CC0 unless otherwise noted. Facts and third-party filing text retain their applicable rights status.

See [LICENSING.md](LICENSING.md) for source terms and separate commercial licensing of the Filiation rights covered.

## How to cite

Use [CITATION.cff](CITATION.cff) for the software. For a future dataset release, cite the version DOI of the snapshot you used. See the [dataset citation guidance](LICENSING.md#how-to-cite).

## Contributing

Issues and source corrections are welcome. For a correction, use the [Source correction form](https://github.com/filiation-hq/filiation/issues/new?template=source-correction.yml). Please use only public K-numbers and FDA PDF page citations as evidence.

External patches and contributed data requiring relicensing rights cannot be applied until Steven confirms the contributor agreement and signing process. See [CONTRIBUTING.md](CONTRIBUTING.md) for how eligible patches are applied and credited.
