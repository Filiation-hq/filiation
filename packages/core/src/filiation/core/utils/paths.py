"""Path resolution utilities for the 3-tier Filiation data layout."""

from pathlib import Path

ROOT_MARKER = "ARCHITECTURE.txt"


def get_repo_root(start: Path | None = None) -> Path:
    """Finds the Filiation repo root by walking up to the directory that holds ARCHITECTURE.txt.

    Args:
        start: Directory to begin the search from; defaults to this module's directory.

    Returns:
        Path: The repo root, or the current working directory if no marker is found.
    """
    current = (start or Path(__file__).parent).resolve()
    for parent in [current, *current.parents]:
        if (parent / ROOT_MARKER).exists():
            return parent
    return Path.cwd()


def get_data_dir(tier: str = "derived", repo_root: Path | None = None) -> Path:
    """Returns the data/<tier> directory under the repo root, creating it if missing.

    Args:
        tier: One of "raw", "interim", or "derived".
        repo_root: Repo root to resolve against; defaults to get_repo_root().

    Returns:
        Path: The existing data tier directory.
    """
    path = (repo_root or get_repo_root()) / "data" / tier
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_raw_dir(repo_root: Path | None = None) -> Path:
    """Returns the git-ignored raw data directory.

    Args:
        repo_root: Repo root to resolve against; defaults to get_repo_root().

    Returns:
        Path: <repo root>/data/raw.
    """
    return get_data_dir("raw", repo_root)


def get_interim_dir(repo_root: Path | None = None) -> Path:
    """Returns the git-ignored interim data directory.

    Args:
        repo_root: Repo root to resolve against; defaults to get_repo_root().

    Returns:
        Path: <repo root>/data/interim.
    """
    return get_data_dir("interim", repo_root)


def get_derived_dir(repo_root: Path | None = None) -> Path:
    """Returns the syncable derived data directory.

    Args:
        repo_root: Repo root to resolve against; defaults to get_repo_root().

    Returns:
        Path: <repo root>/data/derived.
    """
    return get_data_dir("derived", repo_root)
