.PHONY: test lint coverage test-integration
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
SCRIPTS = $(shell $(PYTHON) -c 'import sysconfig; print(sysconfig.get_path("scripts"))')
test:
	$(PYTHON) -m pytest
lint:
	$(PYTHON) -m compileall -q src
	$(PYTHON) -m ruff check src tests docker
	@test -x "$(SCRIPTS)/shellcheck" || { echo "shellcheck missing: install the lint extra" >&2; exit 1; }
	"$(SCRIPTS)/actionlint" -shellcheck "$(SCRIPTS)/shellcheck" .github/workflows/*.yml
coverage:
	$(PYTHON) -m coverage run -m pytest -m 'not integration'
	$(PYTHON) -m coverage report
test-integration:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ENVFUZZ_GAZEBO_TEST=1 $(PYTHON) -m pytest -m integration
