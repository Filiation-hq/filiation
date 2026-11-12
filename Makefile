.PHONY: install install-dev test lint run

# One-time setup: installs both packages in editable mode
install:
	pip install -e packages/core -e packages/pipeline

install-dev:
	pip install -e packages/core -e packages/pipeline pytest ruff==0.17.4

# Run the full test suite
test:
	PYTHONPATH="packages/core/src:packages/pipeline/src" python3 -m pytest

# Lint the whole repo with the pinned ruff version
lint:
	ruff check .

# Run the pipeline CLI (use 'make run CMD=status' etc.)
run:
	stemma-pipeline $(CMD)
