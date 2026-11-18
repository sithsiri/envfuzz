import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
SPEC = importlib.util.spec_from_file_location('replay_fixtures', ROOT / 'docker' / 'replay_fixtures.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
SIGNATURE = ['corridor', 'timeout', False, [1, 0]]


def write_fixture(root, name, signature=SIGNATURE):
    bundle = root / name / 'bundle'
    bundle.mkdir(parents=True)
    (bundle / 'scenario.json').write_text('{}')
    (root / name / 'fixture.json').write_text(json.dumps({
        'schema_version': 1, 'id': name, 'expected_signature': signature,
    }))


def corpus(**fixtures):
    return {'schema_version': 1, 'fixtures': {
        name: {'floor': 1, 'nightly_replays': count, 'minimum_valid_replays': count}
        for name, count in fixtures.items()
    }}


def fake_signature(bundle, layout):
    return ('corridor', 'timeout', False, (1, 0))


class FakeReplay:
    def __init__(self):
        self.calls = []

    def __call__(self, root, output, report, repeats, workers, image):
        self.calls.append((root, repeats, workers, image, [case['id'] for case in report['corpus']]))
        report['corpus'] = [{**case, 'replays': [
            {'bundle': f'corpus-replays/case-{index:04d}/repeat-{number:03d}/bundle',
             'status': 'timeout', 'classification': 'behavior', 'real_time_factor': 0.4, 'matched': True}
            for number in range(1, repeats + 1)
        ]} for index, case in enumerate(report['corpus'], 1)]


def test_replays_each_fixture_its_nightly_count_in_separate_roots(tmp_path):
    fixtures, work = tmp_path / 'fixtures', tmp_path / 'work'
    for name in ('a', 'b', 'c'):
        write_fixture(fixtures, name)
    replay = FakeReplay()

    results = RUNNER.run_fixture_replays(corpus(a=3, b=5, c=3), fixtures, work, 2, 'envfuzz:ci',
                                         replay, fake_signature)

    assert [(call[0], call[1], call[4]) for call in replay.calls] == [
        (work / 'k-03', 3, ['a', 'c']), (work / 'k-05', 5, ['b'])]
    assert all(call[2:4] == (2, 'envfuzz:ci') for call in replay.calls)
    assert (work / 'k-03' / 'fixtures' / 'a' / 'bundle' / 'scenario.json').is_file()
    assert [(item['id'], len(item['replays'])) for item in results['fixtures']] == [
        ('a', 3), ('c', 3), ('b', 5)]
    assert results['fixtures'][0]['replays'][0]['bundle'].startswith('k-03/corpus-replays/')


def test_refuses_to_replay_a_fixture_whose_archived_bundle_changed_signature(tmp_path):
    fixtures = tmp_path / 'fixtures'
    write_fixture(fixtures, 'a', signature=['corridor', 'completed', True, [0, 0]])

    with pytest.raises(ValueError, match='expected signature'):
        RUNNER.run_fixture_replays(corpus(a=3), fixtures, tmp_path / 'work', 1, 'img',
                                   FakeReplay(), fake_signature)


def test_missing_fixture_is_an_error(tmp_path):
    with pytest.raises(ValueError, match='missing'):
        RUNNER.run_fixture_replays(corpus(a=3), tmp_path, tmp_path / 'work', 1, 'img',
                                   FakeReplay(), fake_signature)


def test_empty_corpus_replays_nothing(tmp_path):
    replay = FakeReplay()

    results = RUNNER.run_fixture_replays(corpus(), tmp_path, tmp_path / 'work', 1, 'img',
                                         replay, fake_signature)

    assert results == {'schema_version': 1, 'fixtures': []}
    assert replay.calls == []


def test_main_writes_empty_results_for_the_checked_in_corpus(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'work' / 'results.json'
    monkeypatch.setattr('sys.argv', [
        'replay_fixtures.py', '--corpus', str(ROOT / 'benchmarks' / 'replay-corpus.json'),
        '--fixtures', str(ROOT / 'benchmarks' / 'replay-fixtures'), '--work', str(tmp_path / 'work'),
        '--image', 'envfuzz:ci', '--output', str(output),
    ])

    assert RUNNER.main() == 0
    assert json.loads(output.read_text()) == {'schema_version': 1, 'fixtures': []}
    assert 'nothing to replay' in capsys.readouterr().out
