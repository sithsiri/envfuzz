import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
SPEC = importlib.util.spec_from_file_location('check_replay', ROOT / 'docker' / 'check_replay.py')
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
CORPUS = ROOT / 'benchmarks' / 'replay-corpus.json'
FIXTURE = 'corridor-timeout-01'


def corpus(fixtures):
    return {'schema_version': 1, 'fixtures': fixtures}


def entry(floor=0.95, nightly=3, minimum=3):
    return {'floor': floor, 'nightly_replays': nightly, 'minimum_valid_replays': minimum}


def write_fixture(root, name=FIXTURE, valid=20, matching=20, **admission):
    directory = root / name
    (directory / 'bundle').mkdir(parents=True)
    for file in ('scenario.json', 'run.json', 'result.json'):
        (directory / 'bundle' / file).write_text('{}')
    (directory / 'fixture.json').write_text(json.dumps({
        'schema_version': 1, 'id': name, 'expected_signature': ['corridor', 'timeout', False, [1, 0]],
    }))
    (directory / 'admission.json').write_text(json.dumps({
        'valid_replays': valid, 'matching_replays': matching, 'infrastructure_attempts': 0,
        **admission,
    }))
    return directory


def rows(matched=3, unmatched=0, infrastructure=0):
    return ([{'classification': 'behavior', 'matched': True}] * matched
            + [{'classification': 'behavior', 'matched': False}] * unmatched
            + [{'classification': 'infrastructure', 'matched': False}] * infrastructure)


def results(replays, name=FIXTURE, **extra):
    return {'schema_version': 1, 'fixtures': [{'id': name, 'replays': replays, **extra}]}


def test_admitted_fixture_that_agrees_tonight_passes(tmp_path):
    write_fixture(tmp_path)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path, results(rows(3)))

    assert report['failures'] == []
    assert report['enforced'] is True
    assert report['fixtures'][FIXTURE]['tonight'] == {
        'attempts': 3, 'valid_replays': 3, 'matching_replays': 3, 'infrastructure_attempts': 0,
        'agreement': 1.0,
    }


def test_fixture_below_its_floor_tonight_fails(tmp_path):
    write_fixture(tmp_path)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path, results(rows(2, unmatched=1)))

    assert report['failures'] == [f'{FIXTURE}: tonight 2/3 agreement 0.6667 is below floor 0.95']


def test_fixture_with_too_few_valid_replays_tonight_fails(tmp_path):
    write_fixture(tmp_path)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path,
                                  results(rows(2, infrastructure=1)))

    assert report['failures'] == [
        f'{FIXTURE}: 2 valid replays tonight, at least 3 required (1 infrastructure attempts)']


def test_night_with_only_infrastructure_attempts_fails(tmp_path):
    write_fixture(tmp_path)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path,
                                  results(rows(0, infrastructure=3)))

    assert report['failures'] == [
        f'{FIXTURE}: 0 valid replays tonight, at least 3 required (3 infrastructure attempts)']
    assert report['fixtures'][FIXTURE]['tonight']['agreement'] is None


def test_precomputed_rates_are_ignored_in_favour_of_replay_rows(tmp_path):
    write_fixture(tmp_path, matching=10, reproduction_rate=1.0)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path,
                                  results(rows(0, unmatched=3), reproduction_rate=1.0,
                                          matching_replays=3, valid_replays=3))

    assert report['failures'] == [
        f'{FIXTURE}: admission 10/20 agreement 0.5 is below floor 0.95',
        f'{FIXTURE}: tonight 0/3 agreement 0 is below floor 0.95',
    ]


def test_missing_fixture_directory_fails(tmp_path):
    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path, results(rows(3)))

    assert report['failures'] == [f'{FIXTURE}: frozen fixture is missing fixture.json']


def test_fixture_missing_from_tonights_results_fails(tmp_path):
    write_fixture(tmp_path)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path, None)

    assert report['failures'] == [f'{FIXTURE}: no replays recorded tonight']


@pytest.mark.parametrize(('admission', 'failure'), [
    ({'valid': 19, 'matching': 19}, 'admission has 19 valid replays, at least 20 required'),
    ({'valid': 20, 'matching': 21}, 'admission evidence is malformed'),
    ({'valid': 20.0, 'matching': 20}, 'admission evidence is malformed'),
    ({'valid': True, 'matching': 20}, 'admission evidence is malformed'),
    ({'valid': 20, 'matching': 18}, 'admission 18/20 agreement 0.9 is below floor 0.95'),
])
def test_bad_admission_evidence_fails(tmp_path, admission, failure):
    write_fixture(tmp_path, **admission)

    report = CHECK.check_fixtures(corpus({FIXTURE: entry()}), tmp_path, results(rows(3)))

    assert report['failures'] == [f'{FIXTURE}: {failure}']


def test_empty_corpus_passes_but_reports_that_nothing_is_enforced(tmp_path):
    report = CHECK.check_fixtures(corpus({}), tmp_path, None)

    assert report == {'schema_version': 1, 'enforced': False, 'fixtures': {}, 'failures': []}


@pytest.mark.parametrize('content', [
    '[]',
    '{"schema_version": 2, "fixtures": {}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": 0.9, "nightly_replays": 3, "minimum_valid_replays": 3}}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": NaN, "nightly_replays": 3, "minimum_valid_replays": 3}}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": 1, "nightly_replays": 0, "minimum_valid_replays": 0}}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": 1, "nightly_replays": 21, "minimum_valid_replays": 3}}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": 1, "nightly_replays": 3, "minimum_valid_replays": 4}}}',
    '{"schema_version": 1, "fixtures": {"a": {"floor": 1, "nightly_replays": 3}}}',
    '{"schema_version": 1, "fixtures": {"../a": {"floor": 1, "nightly_replays": 3, "minimum_valid_replays": 3}}}',
    '{"schema_version": 1, "fixtures": {}, "fixtures": {}}',
])
def test_corpus_file_rejects_invalid_entries(tmp_path, content):
    path = tmp_path / 'corpus.json'
    path.write_text(content)

    with pytest.raises(ValueError):
        CHECK.load_corpus(path)


def test_checked_in_corpus_is_valid_and_empty_until_a_fixture_is_admitted():
    # docs/phase-3-verification.md: 0 of 17 valid replays matched, so nothing is admitted.
    assert CHECK.load_corpus(CORPUS) == corpus({})


def run_main(monkeypatch, *arguments):
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr('sys.argv', ['check_replay.py', *map(str, arguments)])
    return CHECK.main()


def test_main_warns_loudly_that_an_empty_corpus_enforces_nothing(tmp_path, monkeypatch, capsys):
    summary = tmp_path / 'summary.md'
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr('sys.argv', ['check_replay.py', '--corpus', str(CORPUS), '--fixtures',
                                     str(tmp_path), '--output', str(tmp_path / 'report.json')])

    assert CHECK.main() == 0
    output = capsys.readouterr().out
    assert '::warning::' in output
    assert 'enforces nothing' in output
    assert 'enforces nothing' in summary.read_text()


def test_main_fails_when_an_admitted_fixture_disagrees(tmp_path, monkeypatch):
    fixtures = tmp_path / 'fixtures'
    write_fixture(fixtures)
    corpus_path = tmp_path / 'corpus.json'
    corpus_path.write_text(json.dumps(corpus({FIXTURE: entry()})))
    results_path = tmp_path / 'results.json'
    results_path.write_text(json.dumps(results(rows(1, unmatched=2))))
    report_path = tmp_path / 'report.json'

    code = run_main(monkeypatch, '--corpus', corpus_path, '--fixtures', fixtures,
                    '--results', results_path, '--output', report_path)

    assert code == 1
    assert json.loads(report_path.read_text())['failures']


@pytest.mark.parametrize('replays', [
    {'schema_version': 1, 'fixtures': {}},
    {'schema_version': 1, 'fixtures': [{'id': FIXTURE, 'replays': {}}]},
    {'schema_version': 1, 'fixtures': [{'id': FIXTURE, 'replays': [{'classification': 'behavior'}]}]},
    {'schema_version': 1, 'fixtures': [{'id': FIXTURE,
                                        'replays': [{'classification': 'odd', 'matched': True}]}]},
    {'schema_version': 1, 'fixtures': [{'id': FIXTURE, 'replays': []}, {'id': FIXTURE, 'replays': []}]},
])
def test_main_rejects_malformed_replay_results_without_a_traceback(tmp_path, monkeypatch, capsys, replays):
    fixtures = tmp_path / 'fixtures'
    write_fixture(fixtures)
    corpus_path = tmp_path / 'corpus.json'
    corpus_path.write_text(json.dumps(corpus({FIXTURE: entry()})))
    results_path = tmp_path / 'results.json'
    results_path.write_text(json.dumps(replays))

    with pytest.raises(SystemExit) as error:
        run_main(monkeypatch, '--corpus', corpus_path, '--fixtures', fixtures, '--results', results_path,
                 '--output', tmp_path / 'report.json')

    assert error.value.code == 2
    assert 'Traceback' not in capsys.readouterr().err
