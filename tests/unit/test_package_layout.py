"""Unit tests that pin the canonical package layout decided in ADR-002.

Task map:
    Input: the repository file tree.
    Output: pass only if packages/*/src/filiation/ is the single source tree.
    Edge cases: stray top-level filiation/ tree; subpackage missing __init__.py
        (setuptools find_packages would silently drop it); an __init__.py at
        the shared filiation/ namespace level (would break core + pipeline co-install).
    Invariant: every directory holding .py files under a package root is a
        regular package, and the filiation namespace itself stays implicit.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = [
    REPO_ROOT / "packages" / "core" / "src" / "filiation" / "core",
    REPO_ROOT / "packages" / "pipeline" / "src" / "filiation" / "pipeline",
    REPO_ROOT / "packages" / "models" / "src" / "filiation" / "models",
]


def test_top_level_duplicate_filiation_tree_does_not_exist():
    # Sample: the repo root. A filiation/ directory here duplicates packages/*/src/filiation/.
    assert not (REPO_ROOT / "filiation").exists()


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


def test_shared_filiation_namespace_has_no_init_file():
    # Sample: packages/core/src/filiation/ and packages/pipeline/src/filiation/. Both install
    # into the same "filiation" namespace, so neither may define filiation/__init__.py.
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "packages").glob("*/src/filiation/__init__.py")
    ]
    assert offenders == []


def test_every_package_root_exists_with_init_file():
    # Sample: the core, pipeline, and models roots. Before #10, packages/models had no
    # src/filiation/models/ directory, only an empty src/cli.py.
    missing = [str(r.relative_to(REPO_ROOT)) for r in PACKAGE_ROOTS if not (r / "__init__.py").exists()]
    assert missing == []


def test_no_source_files_sit_outside_the_filiation_namespace():
    # Sample: every .py file under packages/*/src/. Files directly in src/ (like the old
    # packages/models/src/cli.py) are not importable as filiation.* and break the layout.
    strays = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "packages").glob("*/src/*.py")
    ]
    assert strays == []


def test_models_pyproject_declares_filiation_models_distribution():
    # Sample: packages/models/pyproject.toml. Before #10 it was 0 bytes, so
    # `pip install -e packages/models` failed.
    text = (REPO_ROOT / "packages" / "models" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "filiation-models"' in text
    assert 'where = ["src"]' in text


def test_no_package_still_ships_the_recension_namespace():
    # Sample: packages/*/src/recension/, the namespace before ADR-019. Two namespaces would
    # let stale imports keep working against code that is no longer maintained.
    leftovers = sorted(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "packages").glob("*/src/recension"))
    assert leftovers == []
