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
