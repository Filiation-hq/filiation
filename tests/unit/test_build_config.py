"""Unit tests for build and developer tooling configuration (Makefile, pyproject.toml).

Task map:
    Input: Makefile and pyproject.toml text.
    Output: pass only if `make test` uses the same runner the repo configures (pytest).
    Edge cases: unittest-based runner that silently skips plain pytest functions.
    Invariant: `make test` and `pytest` collect the same tests.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def read_make_target(name: str) -> str:
    """Returns the recipe lines of one Makefile target.

    Args:
        name: Target name, e.g. "test".

    Returns:
        str: The target's recipe text, or "" if the target is absent.
    """
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    match = re.search(rf"^{name}:.*\n((?:\t.*\n?)*)", text, re.MULTILINE)
    return match.group(1) if match else ""


def test_make_test_target_invokes_pytest():
    # Sample: the `test:` recipe in Makefile. It must call pytest, which collects
    # plain-function tests such as tests/unit/test_pr_body_check.py.
    assert "pytest" in read_make_target("test")


def test_make_test_target_does_not_use_unittest_discover():
    # Sample: the `test:` recipe in Makefile. `unittest discover` skips plain
    # pytest functions (it collected 10 of 26 tests before this fix).
    assert "unittest discover" not in read_make_target("test")


def test_make_install_dev_installs_pytest():
    # Sample: the `install-dev:` recipe in Makefile. `make test` needs pytest installed.
    assert "pytest" in read_make_target("install-dev")


def test_make_lint_target_runs_ruff_check():
    # Sample: the `lint:` recipe in Makefile. CI and developers share it, so
    # local lint results match CI.
    assert "ruff check ." in read_make_target("lint")


def test_ci_workflow_runs_make_test_and_make_lint_on_pull_requests():
    # Sample: .github/workflows/ci.yml. It must trigger on pull_request and call
    # the same Makefile targets developers run locally.
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "pull_request" in workflow
    assert "make test" in workflow
    assert "make lint" in workflow


def test_make_install_dev_pins_ruff_version():
    # Sample: the `install-dev:` recipe. An unpinned ruff lets new upstream rules
    # break CI without any code change.
    assert re.search(r"ruff==\d+\.\d+\.\d+", read_make_target("install-dev"))


def test_gitignore_excludes_private_build_in_public_plan():
    # Sample: the repo's own .gitignore. BUILDINPUBLIC.md names competitors and the IP tiers,
    # so it must be ignored even when the repo is public again (#59).
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "BUILDINPUBLIC.md" in [line.strip() for line in lines]


def test_gitignore_excludes_private_vm_notes():
    # Sample: the repo's own .gitignore. VMCONFIG.md holds the Oracle VM's public IP, SSH
    # user, and key path, so it must never reach the remote.
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "VMCONFIG.md" in [line.strip() for line in lines]


def test_citation_points_readers_at_the_public_repository():
    # Sample: CITATION.cff's repository-code. The source repo is private, so a citation
    # naming it sends every reader to a 404; it must name the public mirror (docs/PUBLIC_MIRROR.md).
    text = (REPO_ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert 'repository-code: "https://github.com/recension-hq/recension"' in text
