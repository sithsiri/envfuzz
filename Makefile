.PHONY: test lint test-integration
test:
	python3 -m pytest
lint:
	python3 -m compileall -q src
test-integration:
	python3 -m pytest -m integration
