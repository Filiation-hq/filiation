"""Unit tests that keep declared dependencies in sync with real imports (ADR-004).

Task map:
    Input: each pyproject.toml and the .py files of its package.
    Output: pass only if every declared runtime dependency is imported by that package.
    Edge cases: indirect dependencies (pyarrow is pandas' Parquet engine, never imported
        directly); the root pyproject, which ships no code, must declare none.
    Invariant: a dependency is declared if and only if the package needs it.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INDIRECT = {"pyarrow": "pandas Parquet engine (to_parquet / read_parquet)"}
IMPORT_NAMES = {"pillow": "PIL"}


def declared_dependencies(pyproject: Path) -> list[str]:
    """Parses the [project] dependencies array of a pyproject.toml without tomllib.

    Args:
        pyproject: Path to a pyproject.toml file.

    Returns:
        list[str]: Lowercased distribution names, version specifiers stripped.
    """
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if not match:
        return []
    entries = re.findall(r'"([^"]+)"', match.group(1))
    return [re.split(r"[<>=!~\[; ]", e, maxsplit=1)[0].lower() for e in entries]


def imported_modules(source_dir: Path) -> set[str]:
    """Collects top-level module names imported anywhere under a source directory.

    Args:
        source_dir: Directory to scan recursively for .py files.

    Returns:
        set[str]: Top-level names from `import x` and `from x import y` statements.
    """
    pattern = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_]\w*)", re.MULTILINE)
    return {m for f in source_dir.rglob("*.py") for m in pattern.findall(f.read_text(encoding="utf-8"))}


def unused_dependencies(package: str) -> list[str]:
    """Lists a package's declared dependencies that nothing in its source imports.

    Args:
        package: Directory name under packages/, e.g. "core".

    Returns:
        list[str]: Declared dependencies with no matching import and no INDIRECT reason.
    """
    root = REPO_ROOT / "packages" / package
    imports = imported_modules(root / "src")
    return [
        d for d in declared_dependencies(root / "pyproject.toml")
        if d not in INDIRECT and IMPORT_NAMES.get(d, d) not in imports
    ]


def test_core_declares_only_dependencies_it_imports():
    # Sample: packages/core/pyproject.toml vs imports in packages/core/src/.
    # Before ADR-004 it declared pydantic and duckdb, which nothing imports.
    assert unused_dependencies("core") == []


def test_pipeline_declares_only_dependencies_it_imports():
    # Sample: packages/pipeline/pyproject.toml vs imports in packages/pipeline/src/.
    # Before ADR-004 it declared requests, pillow, and tqdm, which nothing imports.
    assert unused_dependencies("pipeline") == []


def test_root_workspace_declares_no_runtime_dependencies():
    # Sample: root pyproject.toml. It ships no code (ADR-002), so runtime
    # dependencies belong to the packages that import them.
    assert declared_dependencies(REPO_ROOT / "pyproject.toml") == []
