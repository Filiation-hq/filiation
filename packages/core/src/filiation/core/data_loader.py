"""Data loading and verification interface for Filiation datasets."""

import json
from pathlib import Path
from typing import Any

import pandas as pd
from filiation.core.utils.paths import get_derived_dir, get_interim_dir, get_raw_dir


class FiliationDataLoader:
    """Manages access to Filiation derived and raw datasets."""

    def __init__(self, repo_root: Path | None = None):
        self.raw_dir = get_raw_dir(repo_root)
        self.interim_dir = get_interim_dir(repo_root)
        self.derived_dir = get_derived_dir(repo_root)

    def load_nodes(self) -> pd.DataFrame:
        """Loads device_nodes from derived directory."""
        parquet_path = self.derived_dir / "releases" / "device_nodes.parquet"
        csv_path = self.derived_dir / "releases" / "device_nodes.csv"
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        raise FileNotFoundError(f"Device nodes not found in {self.derived_dir / 'releases'}")

    def load_edges(self) -> pd.DataFrame:
        """Loads predicate_edges from derived directory."""
        parquet_path = self.derived_dir / "releases" / "predicate_edges.parquet"
        csv_path = self.derived_dir / "releases" / "predicate_edges.csv"
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        raise FileNotFoundError(f"Predicate edges not found in {self.derived_dir / 'releases'}")

    def load_recalls(self) -> pd.DataFrame:
        """Loads recalls from derived directory."""
        parquet_path = self.derived_dir / "releases" / "recalls.parquet"
        csv_path = self.derived_dir / "releases" / "recalls.csv"
        if parquet_path.exists():
            return pd.read_parquet(parquet_path)
        if csv_path.exists():
            return pd.read_csv(csv_path)
        raise FileNotFoundError(f"Recalls not found in {self.derived_dir / 'releases'}")

    def summary(self) -> dict[str, Any]:
        """Returns statistics on local data store."""
        raw_files = list(self.raw_dir.glob("**/*"))
        interim_files = list(self.interim_dir.glob("**/*"))
        derived_files = list(self.derived_dir.glob("**/*"))
        return {
            "raw_file_count": len([f for f in raw_files if f.is_file()]),
            "interim_file_count": len([f for f in interim_files if f.is_file()]),
            "derived_file_count": len([f for f in derived_files if f.is_file()]),
        }


def main() -> None:
    loader = FiliationDataLoader()
    stats = loader.summary()
    print("=== Filiation Data Store Summary ===")
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
