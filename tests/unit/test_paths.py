"""Unit tests for repo-root and data-tier path resolution in stemma.core.utils.paths.

Task map:
    Input: a starting directory somewhere inside the repo.
    Output: the repo root (the directory holding ARCHITECTURE.txt) and data/<tier> under it.
    Edge cases: a package directory with its own pyproject.toml (packages/core/) sits
        between the start and the root and must not be mistaken for the root.
    Invariant: raw, interim, and derived data always resolve under <repo root>/data/.
"""

from pathlib import Path

from stemma.core.utils.paths import get_raw_dir, get_repo_root

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_fake_repo(tmp_path: Path) -> Path:
    """Creates a minimal repo tree with root and package-level pyproject.toml files.

    Args:
        tmp_path: Empty temporary directory from pytest.

    Returns:
        Path: The package source directory to start resolution from.
    """
    (tmp_path / "ARCHITECTURE.txt").write_text("map", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    package_dir = tmp_path / "packages" / "core"
    source_dir = package_dir / "src" / "stemma" / "core" / "utils"
    source_dir.mkdir(parents=True)
    (package_dir / "pyproject.toml").write_text("[project]", encoding="utf-8")
    return source_dir


def test_get_repo_root_skips_package_pyproject_and_returns_architecture_dir(tmp_path):
    # Sample: fake repo with ARCHITECTURE.txt + pyproject.toml at the root and a second
    # pyproject.toml at packages/core/. Start is packages/core/src/stemma/core/utils/.
    start = build_fake_repo(tmp_path)
    assert get_repo_root(start) == tmp_path


def test_get_repo_root_from_installed_source_returns_real_repo_root():
    # Sample: no argument, so resolution starts from paths.py inside packages/core/src/.
    # The real repo root holds ARCHITECTURE.txt; packages/core/ must not be returned.
    assert get_repo_root() == REPO_ROOT


def test_get_raw_dir_resolves_under_repo_root_data_raw(tmp_path):
    # Sample: explicit repo_root of an empty temp dir. Expect <tmp>/data/raw, created.
    raw = get_raw_dir(tmp_path)
    assert raw == tmp_path / "data" / "raw"
    assert raw.is_dir()
