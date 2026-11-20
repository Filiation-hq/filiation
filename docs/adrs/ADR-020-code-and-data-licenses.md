# ADR-020: License the code under AGPL-3.0-only and the data under CC BY-NC 4.0

**Date:** 2026-11-20
**Status:** Accepted
**Author:** Steven Rocca

## Decision
The code is licensed `AGPL-3.0-only` and the data `CC-BY-NC-4.0`. The code covers code, tests, notebooks and docs. The data covers everything under `data/` and every dataset release. Commercial use of the data needs a separate license, and outside code contributions need a contributor license agreement (CLA), so the project can offer both under commercial terms. This records a choice made on 2026-11-17 and shipped in #135 (`LICENSE`, `LICENSE-DATA`, `LICENSING.md`).

## Context
#60: the repository was public for a day with no license, and it reopens through the public mirror. With no license, nobody may legally reuse the code or data, which defeats an open dataset. A license is also hard to take back: anything released under it stays usable under those terms.

The build-in-public plan sets the constraint. Openness and measured accuracy are the moat, but a paid tier must stay possible, and the predicate simulator (Stage 7) is its likely home. The plan's own comparison, made 2026-11-15:
- CC BY maximizes citations but rules out ever selling a data license, so a paid tier would have to be a service.
- OpenSanctions funds itself with CC BY-NC, free for journalists and researchers, plus paid commercial licenses.

FDA's own records and 510(k) summaries are not ours to license. These licenses cover only what Filiation adds: links, roles, confidence, recall linkage, accuracy measurements and the software.

## Options Considered
1. **Data: CC BY 4.0.** Anyone may reuse it, commercially too, with attribution.
2. **Data: CC BY-NC 4.0, plus paid commercial licenses.** The OpenSanctions model.
3. **Data: ODbL.** Share-alike for databases: derived databases must stay open, and commercial use is allowed.
4. **Code: AGPL-3.0-only.** Anyone who runs a modified version as a network service must publish their changes.
5. **Code: a permissive license (MIT or Apache-2.0).** Any reuse is allowed, closed forks included.

## Tradeoffs
| Criterion | CC BY | CC BY-NC + commercial | ODbL |
|---|---|---|---|
| Free for research, journalism, teaching | Yes | Yes | Yes |
| A commercial data license can be sold | No | Yes | Hardly: share-alike already allows commercial use |
| Counts as "open" under the Open Definition | Yes | No: non-commercial terms are excluded | Yes |
| Reuse friction for citations and aggregators | Lowest | Some will not include NC data | Share-alike obligations |

| Criterion | AGPL-3.0-only | MIT / Apache-2.0 |
|---|---|---|
| A competitor can run a closed, modified copy as a service | No | Yes |
| Adoption inside companies | Lower: many ban AGPL | Highest |
| Dual licensing possible | Yes, with a CLA | Not needed |

## Consequences
- `LICENSE`, `LICENSE-DATA` and `LICENSING.md` are the source of truth. Every `pyproject.toml` declares `AGPL-3.0-only`, and the README and the public README state both licenses.
- Commercial requests go to the contact address in `LICENSING.md`.
- The data is not "open data" under the Open Definition. Say "free for non-commercial use", never "open data", in the dataset card and in posts.
- Zenodo releases carry CC BY-NC 4.0, and a published release keeps its license even if a later one changes.
- Outside pull requests need a signed CLA before they're applied (`CONTRIBUTING.md`).
- Not changed: FDA's public records stay public domain, and Filiation does not redistribute the summary PDFs.

## Definitions Altered
- No `CONTEXT.md` terms change.
- Sign-off: the developer decided this on 2026-11-17. The data science role is unassigned, so its review is pending (AGENTS.md §13). #60 asked for the DS partner's agreement as well.
