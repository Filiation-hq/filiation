# Contributing to Recension

Thanks for helping. This repository is the **public mirror** of Recension. Development happens in a private repository, and this one is generated from it every Monday. That changes how contributions land.

## Issues

Open an issue for:
- source corrections: something we published disagrees with an FDA document. Use the **Source correction** form;
- bugs;
- surprising openFDA data, such as missing fields, odd dates, or records that disagree;
- questions about the method;
- proposals.

Please include the snapshot or export date you saw the problem in, and the record IDs (K-number, `product_res_number`, `res_event_number`) so it can be reproduced.

## Pull requests

1. **Open an issue first**, so the change can be discussed before you write it.
2. **Sign the contributor license agreement (CLA).** Recension may also be offered under commercial licenses, so an outside contribution can't be applied without one. A PR without a signed CLA stays open but can't be applied. The signing process is linked here once it is set up; until then, ask in your issue.
3. **Open the PR against `main`** and fill in the PR template; a check enforces it.
   - `Closes #N`: your issue's number.
   - `Milestone: None`.
   - `Base branch: main`.
   - **Edge cases:** one line per case, saying how the code handles it and which test proves it.
4. **Write tests first.** New tests must fail without your change. A check called the TDD guard runs each new test against `main` and flags any that already pass, as well as tests without real assertions.
5. **Run `make test` and `make lint` before pushing.**

### What happens next

- Your PR is reviewed here.
- Once it's approved and the CLA is signed, the patch is applied to the private repository with you credited as a co-author (`Co-authored-by: Your Name <you@example.com>`).
- The PR is then closed with a link to the issue. Your commit appears here at the next Monday update. This repository is generated from the private one, so PRs are never merged here directly.

## Licensing of contributions

- **Code** contributions are licensed under [AGPL-3.0-only](LICENSE).
- **Data** contributions (for example, corrections to extracted links) are licensed under [CC BY-NC 4.0](LICENSE-DATA).

Both are subject to the CLA. See [`LICENSING.md`](LICENSING.md).
