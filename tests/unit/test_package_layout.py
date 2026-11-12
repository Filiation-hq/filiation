"""Unit tests that pin the canonical package layout decided in ADR-002.

Task map:
    Input: the repository file tree.
    Output: pass only if packages/*/src/stemma/ is the single source tree.
    Edge cases: stray top-level stemma/ tree; subpackage missing __init__.py
        (setuptools find_packages would silently drop it); an __init__.py at
        the shared stemma/ namespace level (would break core + pipeline co-install).
    Invariant: every directory holding .py files under a package root is a
        regular package, and the stemma namespace itself stays implicit.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = [
    REPO_ROOT / "packages" / "core" / "src" / "stemma" / "core",
    REPO_ROOT / "packages" / "pipeline" / "src" / "stemma" / "pipeline",
]


def test_top_level_duplicate_stemma_tree_does_not_exist():
    # Sample: the repo root. A stemma/ directory here duplicates packages/*/src/stemma/.
    assert not (REPO_ROOT / "stemma").exists()


def test_every_source_directory_under_package_roots_has_init_file():
    # Sample: all directories under packages/core and packages/pipeline package roots
    # that contain .py files (e.g. core/schemas/, pipeline/crawler/).
    missing = [
        str(d.relative_to(REPO_ROOT))
        for root in PACKAGE_ROOTS
        for d in [root, *[p for p in root.rglob("*") if p.is_dir()]]
        if "__pycache__" not in d.parts and any(d.glob("*.py")) and not (d / "__init__.py").exists()
    ]
    assert missing == []


def test_shared_stemma_namespace_has_no_init_file():
    # Sample: packages/core/src/stemma/ and packages/pipeline/src/stemma/. Both install
    # into the same "stemma" namespace, so neither may define stemma/__init__.py.
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "packages").glob("*/src/stemma/__init__.py")
    ]
    assert offenders == []
