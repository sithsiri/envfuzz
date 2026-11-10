.PHONY: test lint test-integration
test:
	python3 -m pytest
lint:
	python3 -m compileall -q src
test-integration:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ENVFUZZ_GAZEBO_TEST=1 python3 -m pytest -m integration
