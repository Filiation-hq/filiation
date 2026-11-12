# Stemma Maturity Roadmap

Each stage is a GitHub milestone. A stage begins only once the previous stage's exit criteria are met; dates never decide this (ADR-009). Until Stage 2b, data science work is analysis only: profiling, labeling, accuracy measurement, and descriptive statistics, with no model training.

Ingestion stays batch at every stage (ADR-008).

| Stage | Goal | Steven (DE) | Alden (DS) | Exit criteria |
|---|---|---|---|---|
| **0: Foundation** | The repo is trustworthy | Repo Bootstrap stack merged; `tests` and `lint` required on `main` | Review data contracts and `CONTEXT.md` definitions | Repo Bootstrap milestone closed; CI blocks failing merges |
| **1: Batch Ingestion** | Complete, reproducible raw data | Full openFDA 510(k) and recall history; idempotent incremental pulls; CDRH PDF crawl; scheduled weekly GitHub Actions run; versioned raw snapshots | Profile openFDA metadata (counts by era, product code, panel, missing fields); define data-quality rules; design the gold set | **TBD (Alden + Steven):** number of consecutive unattended weekly runs; row counts reconcile with openFDA `meta.results.total` |
| **2a: Extraction & Graph** | An accurate, measured predicate graph | Extraction across all 5 eras, OCR repair, K-number and recall linkage, versioned derived release | Label the gold set; measure per-era precision and recall; descriptive graph stats; JAMA replication (`01_jama_replication.ipynb`) | **TBD (Alden):** per-era precision and recall thresholds, met on a frozen snapshot. Eras below threshold are excluded from 2b by name |
| **2b: Modeling** | A first risk model | Point-in-time training snapshots; feature data | Survival baseline (library per ADR-007), immortal-time-safe features, out-of-time backtests | Baseline beats the product-code-only baseline in backtests |
| **3: Productized Score** | An explainable public output | Release feed and changelog; evaluate an orchestrator (ADR-008) | Calibration, bias checks, score explanations | Calibrated score shipped with documented methodology |
| **4: Operational Maturity** | Others depend on it | Monitoring, alerting, freshness commitments, R2 sync | Drift monitoring, recalibration schedule | External consumers exist, and incidents have an owner |
