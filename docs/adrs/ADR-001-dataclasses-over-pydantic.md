# ADR-001: Use dataclasses over Pydantic for core schema contracts

**Date:** 2026-11-11
**Status:** Accepted
**Author:** Steven Rocca

## Decision
Core data contracts (`DeviceNode`, `PredicateEdge`, `RecallRecord`) use Python `dataclasses` with manual validation in `__post_init__`, not Pydantic `BaseModel`.

## Context
The schemas need to serialize to Parquet, CSV, and JSON, and be constructed from openFDA API responses, PDF parser outputs, and DuckDB query results. Pydantic is listed as a dependency for future use but is not used in the current schema layer.

## Options Considered
1. **Pydantic BaseModel** — Full validation, serialization, and schema generation built in.
2. **Python dataclasses** — Lightweight, no dependency, explicit control over normalization.

## Tradeoffs
| Criterion | Pydantic | dataclasses |
|---|---|---|
| Validation depth | Automatic, declarative | Manual in `__post_init__` |
| Dependency weight | Adds pydantic + pydantic-core | Zero additional dependencies |
| Serialization | Built-in `.model_dump()` | Manual `to_dict()` / `from_dict()` |
| DuckDB/Parquet compat | Requires adapter | Direct dict conversion |
| Startup time | Slower (model compilation) | Faster |

## Consequences
- `to_dict()` and `from_dict()` class methods are the serialization interface.
- Validation logic (K-number normalization, confidence clamping, enum coercion) lives in `__post_init__`.
- If validation complexity grows significantly, this ADR should be revisited in favor of Pydantic.

## Definitions Altered
- None. This is a foundational decision for the schema layer.
