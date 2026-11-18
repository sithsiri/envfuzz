import ast
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding='utf-8'))


def steps(workflow, job):
    return workflow['jobs'][job]['steps']


def step(workflow, job, name):
    return next(item for item in steps(workflow, job) if item.get('name') == name)


def step_index(workflow, job, name):
    return next(index for index, item in enumerate(steps(workflow, job)) if item.get('name') == name)


def upload_index(workflow, job):
    return next(index for index, item in enumerate(steps(workflow, job))
                if item.get('uses', '').startswith('actions/upload-artifact@'))


# Import name -> distribution name, where they differ.
DISTRIBUTIONS = {'yaml': 'pyyaml'}


@pytest.mark.parametrize('name', ['verify.yml', 'release-images.yml'])
def test_workflow_steps_use_bash_with_pipefail_and_upload_evidence_on_failure(name):
    workflow = load(name)

    assert workflow['defaults']['run']['shell'] == 'bash'
    uploads = [item for job in workflow['jobs'] for item in steps(workflow, job)
               if item.get('uses', '').startswith('actions/upload-artifact@')]
    assert uploads
    assert all(item.get('if') == 'always()' for item in uploads)


def test_push_and_pull_request_run_lint_tests_and_coverage_gate():
    # Imported here so Python 3.10 fails this test instead of skipping the module.
    import tomllib

    workflow = load('verify.yml')

    assert {'push', 'pull_request', 'schedule'} <= set(workflow['on'])
    commands = '\n'.join(item.get('run', '') for item in steps(workflow, 'unit'))
    assert "'.[test,lint]'" in commands
    assert 'make lint' in commands
    assert '--junitxml' in commands
    assert 'coverage report' in commands
    assert 'ruff check' in (ROOT / 'Makefile').read_text()
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert project['tool']['coverage']['report']['fail_under'] == 85
    assert any(dependency.startswith('hypothesis') for dependency in
               project['project']['optional-dependencies']['test'])
    assert {'ruff==0.17.4', 'actionlint-py==1.7.12.25', 'shellcheck-py==0.11.0.1'} <= set(
        project['project']['optional-dependencies']['lint'])


def test_make_lint_runs_actionlint_with_a_required_shellcheck():
    lint = (ROOT / 'Makefile').read_text().split('lint:', 1)[1].split('\ncoverage:', 1)[0]

    assert 'test -x "$(SCRIPTS)/shellcheck"' in lint
    assert '"$(SCRIPTS)/actionlint" -shellcheck "$(SCRIPTS)/shellcheck"' in lint
    assert lint.index('test -x') < lint.index('/actionlint"')


def test_nightly_runs_on_schedule_or_on_demand():
    workflow = load('verify.yml')

    nightly_input = workflow['on']['workflow_dispatch']['inputs']['nightly']
    assert nightly_input['type'] == 'boolean'
    assert nightly_input['default'] is False
    assert workflow['jobs']['headless']['env']['NIGHTLY'] == (
        "${{ github.event_name == 'schedule' || inputs.nightly == true }}")
    assert step(workflow, 'headless', 'Install host CLI')['if'] == "env.NIGHTLY == 'true'"


def test_nightly_replays_frozen_fixtures_and_enforces_replay_and_benchmark_gates():
    workflow = load('verify.yml')

    replays = step(workflow, 'headless', 'Frozen fixture replays')
    replay = step(workflow, 'headless', 'Required replay tolerance')
    benchmark = step(workflow, 'headless', 'Benchmark regression')

    for check in (replays, replay, benchmark):
        assert check['if'] == "${{ !cancelled() && steps.install.outcome == 'success' }}"
    assert '--corpus benchmarks/replay-corpus.json' in replays['run']
    assert '--results runs/ci/fixture-replays/results.json' in replay['run']
    assert '--corpus benchmarks/replay-corpus.json' in replay['run']
    assert '--thresholds benchmarks/nightly-thresholds.json' in benchmark['run']
    assert (step_index(workflow, 'headless', 'Frozen fixture replays')
            < step_index(workflow, 'headless', 'Required replay tolerance'))
    for path in ('docker/replay_fixtures.py', 'docker/check_replay.py', 'benchmarks/replay-corpus.json',
                 'docker/check_benchmark.py', 'benchmarks/nightly-thresholds.json'):
        assert (ROOT / path).is_file()


def test_integration_basetemp_stays_inside_the_container():
    run = step(load('verify.yml'), 'headless', 'Headless integration')['run']

    assert '--basetemp /tmp/pytest' in run
    assert '--basetemp /verification' not in run
    assert 'chmod -R a+rwX /verification' in run
    assert 'exit "$status"' in run


@pytest.mark.parametrize(('name', 'job', 'directory', 'writers'), [
    ('verify.yml', 'headless', 'runs/ci',
     ['Headless integration', 'Nightly campaign and replay diagnostics', 'Frozen fixture replays']),
    ('release-images.yml', 'architecture', 'release-evidence', ['Smoke run']),
])
def test_root_written_evidence_is_chowned_before_it_is_read_or_uploaded(name, job, directory, writers):
    workflow = load(name)

    chown = step(workflow, job, 'Fix evidence ownership')
    position = step_index(workflow, job, 'Fix evidence ownership')
    assert chown['if'] == 'always()'
    assert f'sudo chown -R "$(id -u):$(id -g)" {directory}' in chown['run']
    assert all(step_index(workflow, job, writer) < position for writer in writers)
    assert position < upload_index(workflow, job)


def test_release_manifest_is_written_after_chown_and_before_the_candidate_push():
    workflow = load('release-images.yml')

    order = [step_index(workflow, 'architecture', name) for name in (
        'Smoke run', 'Fix evidence ownership', 'Write release manifest', 'Push verified candidate')]
    assert order == sorted(order)
    smoke = step(workflow, 'architecture', 'Smoke run')['run']
    assert ' doctor ' in smoke and 'run examples/scenario.yaml' in smoke


def test_architecture_jobs_push_only_candidates_and_version_tags_wait_for_both():
    workflow = load('release-images.yml')

    architectures = {item['architecture'] for item in
                     workflow['jobs']['architecture']['strategy']['matrix']['include']}
    push = step(workflow, 'architecture', 'Push verified candidate')['run']
    publish = workflow['jobs']['publish']
    tags = step(workflow, 'publish', 'Publish version tags')['run']
    assert architectures == {'amd64', 'arm64'}
    assert re.search(r'docker push "[^"]*:candidate-\$GITHUB_SHA-\$IMAGE_ARCH"', push)
    assert publish['needs'] == 'architecture'
    assert 'imagetools create --tag "$repository:$RELEASE_VERSION-$arch" "$candidate-$arch"' in tags
    assert '"$candidate-amd64" "$candidate-arm64"' in tags


def test_release_attaches_manifests_and_notes_after_publishing_tags():
    workflow = load('release-images.yml')

    job = workflow['jobs']['publish']
    notes = step(workflow, 'publish', 'Attach version manifests and publish release notes')['run']
    assert job['permissions']['contents'] == 'write'
    assert "--jq '.body // \"\"'" in notes
    assert 'releases/generate-notes' in notes
    assert 'release_evidence.py notes' in notes
    assert 'gh release upload' in notes
    assert 'gh release edit' in notes
    assert (step_index(workflow, 'publish', 'Publish version tags')
            < step_index(workflow, 'publish', 'Attach version manifests and publish release notes'))


def module_level_imports(path):
    modules = set()
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Import):
            modules |= {alias.name.split('.')[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split('.')[0])
    return modules


def test_every_third_party_test_import_is_pinned_in_the_image():
    # The headless job collects every tests/*.py inside the image, so a module-level
    # import that the image lacks breaks `pytest -m integration` at collection.
    local = {path.stem for path in (ROOT / 'tests').glob('*.py')} | {'envfuzz'}
    imported = set().union(*(module_level_imports(path) for path in (ROOT / 'tests').glob('*.py')))
    third_party = {DISTRIBUTIONS.get(module, module).lower()
                   for module in imported - set(sys.stdlib_module_names) - local}
    dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
    pip_line = next(line for line in dockerfile.split('&&') if 'pip install' in line and '==' in line)
    pinned = {name.lower().replace('_', '-') for name in re.findall(r'([A-Za-z0-9_.-]+)==\S+', pip_line)}

    assert {'hypothesis', 'pytest', 'pyyaml'} <= third_party
    assert third_party <= pinned, f'unpinned in the image: {sorted(third_party - pinned)}'
