# Licensing

Recension is released under two licenses: one for the code and one for the data.

| What | License | File |
|---|---|---|
| Source code, tests, notebooks and docs in this repository (everything outside `data/`) | GNU Affero General Public License v3.0 only (`AGPL-3.0-only`) | [`LICENSE`](LICENSE) |
| Data files under `data/` and dataset releases: predicate edges, device nodes, recall linkage, accuracy reports and snapshot files, including copies published on Zenodo | Creative Commons Attribution-NonCommercial 4.0 International (`CC-BY-NC-4.0`) | [`LICENSE-DATA`](LICENSE-DATA) |

Copyright © 2026 Steven Rocca.

## Code (AGPL-3.0-only)

You can use, study, modify and share the code. If you distribute a modified version, or run one as a service that other people use over a network, you must make your modified source available under the same license.

## Data (CC BY-NC 4.0)

You can copy, share and adapt Recension datasets for non-commercial purposes, including academic research, journalism, teaching and personal projects, as long as you credit Recension and cite the snapshot you used.

Commercial use needs a separate license. This includes using the data in a paid product or service, in client work, or inside a company's internal tools. Contact recensionhq@gmail.com.

### How to cite

Cite the specific snapshot you used, by its version DOI, so your numbers can be reproduced exactly. To refer to Recension in general, use the concept DOI, which always resolves to the latest release.

> Rocca, S. (2026). *Recension: the FDA 510(k) predicate lineage graph* (snapshot YYYY-MM-DD) [Data set]. Zenodo. https://doi.org/10.5281/zenodo.XXXXXXX

## What these licenses don't cover

The licenses above cover what Recension adds: extracted predicate links, role labels, confidence scores, recall linkage, accuracy measurements and the software. They don't cover FDA's own records or the 510(k) summary documents, which Recension doesn't redistribute. Each extracted link includes only a short excerpt of the source text so it can be checked against the original.

Nothing in Recension's data is a finding about the safety of any device.
