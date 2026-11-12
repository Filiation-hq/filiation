.PHONY: install install-dev test run

# One-time setup: installs both packages in editable mode
install:
	pip install -e packages/core -e packages/pipeline

install-dev:
	pip install -e packages/core -e packages/pipeline pytest ruff

# Run the full test suite
test:
	PYTHONPATH="packages/core/src:packages/pipeline/src" python3 -m unittest discover -s tests/unit -v

# Run the pipeline CLI (use 'make run CMD=status' etc.)
run:
	stemma-pipeline $(CMD)
