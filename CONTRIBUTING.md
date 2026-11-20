# Contributing to Filiation

Thanks for helping. This repository is the **public mirror** of Filiation. Development happens in a private repository, and this one is generated from it every Monday. That changes how contributions land.

## Issues

Open an issue for:
- source corrections: something we published disagrees with an FDA document. Use the [Source correction form](https://github.com/filiation-hq/filiation/issues/new?template=source-correction.yml) with public K-numbers and FDA PDF page citations only;
- bugs;
- surprising openFDA data, such as missing fields, odd dates, or records that disagree;
- questions about the method;
- proposals.

For data and ingestion issues, include the snapshot or export date you saw the problem in, and the record IDs (K-number, `product_res_number`, `res_event_number`) so it can be reproduced.

## Pull requests

External patches and contributed data requiring relicensing rights cannot be applied until Steven confirms the contributor agreement and signing process. For now, open an issue to discuss your proposed change.

Once Steven confirms the process and the applicable agreement is signed:

1. **Discuss the change in an issue first.**
2. **Open the PR against `main`** and fill in the PR template; a check enforces it.
   - `Closes #N`: your issue's number.
   - `Milestone: None`.
   - `Base branch: main`.
   - **Edge cases:** one line per case, saying how the code handles it and which test proves it.
3. **Write tests first.** New tests must fail without your change. A check called the TDD guard runs each new test against `main` and flags any that already pass, as well as tests without real assertions.
4. **Run `make test` and `make lint` before pushing.**

### What happens next

- Your PR is reviewed here.
- Once it's approved and the CLA is signed, the patch is applied to the private repository with your authorship retained or co-author credit added if the commit is changed.
- The PR is then closed with a link to the issue. Your commit appears here at the next Monday update. This repository is generated from the private one, so PRs are never merged here directly.

## Licensing of contributions

- **Code** contributions are licensed under [AGPL-3.0-only](LICENSE).
- **Data** contributions (for example, corrections to extracted links) are covered by [CC BY-NC 4.0](LICENSE-DATA) to the extent Filiation holds rights in the released dataset.

The contributor agreement must cover commercial relicensing when those rights are needed. See [`LICENSING.md`](LICENSING.md).
