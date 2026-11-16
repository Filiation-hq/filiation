"""Unit tests that pin the canonical package layout decided in ADR-002.

Task map:
    Input: the repository file tree.
    Output: pass only if packages/*/src/recension/ is the single source tree.
    Edge cases: stray top-level recension/ tree; subpackage missing __init__.py
        (setuptools find_packages would silently drop it); an __init__.py at
        the shared recension/ namespace level (would break core + pipeline co-install).
    Invariant: every directory holding .py files under a package root is a
        regular package, and the recension namespace itself stays implicit.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = [
    REPO_ROOT / "packages" / "core" / "src" / "recension" / "core",
    REPO_ROOT / "packages" / "pipeline" / "src" / "recension" / "pipeline",
    REPO_ROOT / "packages" / "models" / "src" / "recension" / "models",
]


def test_top_level_duplicate_recension_tree_does_not_exist():
    # Sample: the repo root. A recension/ directory here duplicates packages/*/src/recension/.
    assert not (REPO_ROOT / "recension").exists()


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


def test_shared_recension_namespace_has_no_init_file():
    # Sample: packages/core/src/recension/ and packages/pipeline/src/recension/. Both install
    # into the same "recension" namespace, so neither may define recension/__init__.py.
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "packages").glob("*/src/recension/__init__.py")
    ]
    assert offenders == []


def test_every_package_root_exists_with_init_file():
    # Sample: the core, pipeline, and models roots. Before #10, packages/models had no
    # src/recension/models/ directory, only an empty src/cli.py.
    missing = [str(r.relative_to(REPO_ROOT)) for r in PACKAGE_ROOTS if not (r / "__init__.py").exists()]
    assert missing == []


def test_no_source_files_sit_outside_the_recension_namespace():
    # Sample: every .py file under packages/*/src/. Files directly in src/ (like the old
    # packages/models/src/cli.py) are not importable as recension.* and break the layout.
    strays = [
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "packages").glob("*/src/*.py")
    ]
    assert strays == []


def test_models_pyproject_declares_recension_models_distribution():
    # Sample: packages/models/pyproject.toml. Before #10 it was 0 bytes, so
    # `pip install -e packages/models` failed.
    text = (REPO_ROOT / "packages" / "models" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "recension-models"' in text
    assert 'where = ["src"]' in text
