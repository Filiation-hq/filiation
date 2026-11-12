"""Path resolution utilities for the 3-tier Stemma data layout."""

from pathlib import Path
from typing import Optional


def get_repo_root() -> Path:
    """Finds the root repository directory of Stemma."""
    # Walk upwards looking for pyproject.toml or ARCHITECTURE.txt
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        if (parent / "ARCHITECTURE.txt").exists() or (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def get_data_dir(tier: str = "derived", repo_root: Optional[Path] = None) -> Path:
    """Returns the path for raw, interim, or derived data directories."""
    root = repo_root or get_repo_root()
    path = root / "data" / tier
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_raw_dir(repo_root: Optional[Path] = None) -> Path:
    return get_data_dir("raw", repo_root)


def get_interim_dir(repo_root: Optional[Path] = None) -> Path:
    return get_data_dir("interim", repo_root)


def get_derived_dir(repo_root: Optional[Path] = None) -> Path:
    return get_data_dir("derived", repo_root)
