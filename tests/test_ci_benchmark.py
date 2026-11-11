import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
MODULE_PATH = ROOT / 'docker' / 'check_benchmark.py'
SPEC = importlib.util.spec_from_file_location('check_benchmark', MODULE_PATH)
BENCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BENCH)
THRESHOLDS = ROOT / 'benchmarks' / 'nightly-thresholds.json'


def write_attempt(root, case, attempt, classification, rate=None, mission_rate=None, nested=True):
    bundle = root / f'case-{case:06d}' / f'attempt-{attempt:06d}'
    if nested:
        bundle = bundle / 'bundle'
    bundle.mkdir(parents=True)
    (bundle / 'result.json').write_text(json.dumps({
        'classification': classification, 'real_time_factor': rate,
    }))
    if mission_rate is not None:
        (bundle / 'metrics.json').write_text(json.dumps({'mission_real_time_factor': mission_rate}))


def write_thresholds(path, metrics):
    path.write_text(json.dumps({'schema_version': 1, 'metrics': metrics}))
    return path


def test_measures_campaign_speed_throughput_infrastructure_and_agreement(tmp_path):
    write_attempt(tmp_path, 1, 1, 'infrastructure')
    write_attempt(tmp_path, 1, 2, 'behavior', 0.3, 0.6)
    write_attempt(tmp_path, 2, 1, 'behavior', 0.5, 0.8, nested=False)
    write_attempt(tmp_path, 3, 1, 'interrupted')
    (tmp_path / 'analysis.json').write_text(json.dumps({'corpus': [
        {'valid_replays': 3, 'matching_replays': 1},
        {'valid_replays': 1, 'matching_replays': 1},
    ]}))
    replay = tmp_path / 'corpus-replays' / 'case-0001' / 'repeat-001' / 'bundle'
    replay.mkdir(parents=True)
    (replay / 'result.json').write_text(json.dumps({'classification': 'behavior', 'real_time_factor': 9}))

    metrics = BENCH.measure_campaign(tmp_path, wall_seconds=120)

    assert metrics == {
        'median_end_to_end_real_time_factor': pytest.approx(0.4),
        'median_mission_real_time_factor': pytest.approx(0.7),
        'infrastructure_attempt_rate': pytest.approx(1 / 3),
        'episodes_per_minute': pytest.approx(1.0),
        'diagnostic_replay_agreement': pytest.approx(0.5),
    }


def test_missing_measurements_are_reported_as_none(tmp_path):
    metrics = BENCH.measure_campaign(tmp_path, wall_seconds=None)

    assert set(metrics.values()) == {None}


def test_gated_metric_below_minimum_is_a_regression():
    thresholds = {'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'}}

    report = BENCH.evaluate({'median_end_to_end_real_time_factor': 0.05}, thresholds)

    assert report['metrics']['median_end_to_end_real_time_factor']['status'] == 'fail'
    assert report['failures'] == ['median_end_to_end_real_time_factor: 0.05 is below minimum 0.1']


def test_gated_metric_above_maximum_is_a_regression():
    thresholds = {'infrastructure_attempt_rate': {'maximum': 0.2, 'source': 'docs'}}

    report = BENCH.evaluate({'infrastructure_attempt_rate': 0.5}, thresholds)

    assert report['failures'] == ['infrastructure_attempt_rate: 0.5 is above maximum 0.2']


def test_gated_metric_that_was_not_measured_fails():
    thresholds = {'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'}}

    report = BENCH.evaluate({'median_end_to_end_real_time_factor': None}, thresholds)

    assert report['failures'] == ['median_end_to_end_real_time_factor: not measured']


def test_objectives_and_references_are_tracked_without_failing():
    thresholds = {
        'median_mission_real_time_factor': {'objective_minimum': 5.0, 'reference': 0.66, 'source': 'adr'},
        'episodes_per_minute': {'source': 'tracked only'},
    }

    report = BENCH.evaluate({'median_mission_real_time_factor': 0.7, 'episodes_per_minute': None},
                            thresholds)

    row = report['metrics']['median_mission_real_time_factor']
    assert report['failures'] == []
    assert row['status'] == 'tracked'
    assert row['objective_met'] is False
    assert row['change_from_reference'] == pytest.approx(0.04)
    assert report['metrics']['episodes_per_minute']['status'] == 'not measured'


def test_passing_gate_reports_pass():
    thresholds = {'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'}}

    report = BENCH.evaluate({'median_end_to_end_real_time_factor': 0.42}, thresholds)

    assert report['metrics']['median_end_to_end_real_time_factor']['status'] == 'pass'
    assert report['failures'] == []


GATE = {'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'}}


@pytest.mark.parametrize('metrics', [
    {},
    {'episodes_per_minute': {'source': 'x'}},
    {'median_end_to_end_real_time_factor': {'reference': 0.4, 'source': 'gate removed'}},
    {'median_end_to_end_real_time_factor': {'minimum': float('nan'), 'source': 'x'}},
    {**GATE, 'episodes_per_minute': {'source': 'x', 'reference': float('inf')}},
    {**GATE, 'unknown_metric': {'source': 'x'}},
    {**GATE, 'episodes_per_minute': {'minimum': 1}},
    {**GATE, 'episodes_per_minute': {'source': 'x', 'floor': 1}},
    {**GATE, 'episodes_per_minute': {'source': 'x', 'minimum': 'fast'}},
])
def test_thresholds_file_rejects_invalid_entries(tmp_path, metrics):
    path = write_thresholds(tmp_path / 'thresholds.json', metrics)

    with pytest.raises(ValueError):
        BENCH.load_thresholds(path)


def test_checked_in_thresholds_only_gate_the_documented_speed_floor():
    thresholds = BENCH.load_thresholds(THRESHOLDS)

    gated = {name: entry for name, entry in thresholds.items() if {'minimum', 'maximum'} & entry.keys()}
    # docs/releases.md documents the 0.1 nightly floor; nothing else has a measured gate.
    assert gated == {'median_end_to_end_real_time_factor': thresholds['median_end_to_end_real_time_factor']}
    assert gated['median_end_to_end_real_time_factor']['minimum'] == 0.1
    assert set(thresholds) == set(BENCH.METRICS)
    assert all(entry['source'] for entry in thresholds.values())


def test_markdown_summary_lists_every_metric_and_the_verdict():
    thresholds = {'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'},
                  'episodes_per_minute': {'source': 'tracked'}}
    report = BENCH.evaluate({'median_end_to_end_real_time_factor': 0.05, 'episodes_per_minute': 2.5},
                            thresholds)

    summary = BENCH.render_markdown(report)

    assert 'Benchmark regression' in summary
    assert '| median_end_to_end_real_time_factor | 0.05 |' in summary
    assert '| episodes_per_minute | 2.5 |' in summary
    assert 'below minimum 0.1' in summary


def test_main_writes_report_and_step_summary_and_fails_on_regression(tmp_path, monkeypatch):
    write_attempt(tmp_path, 1, 1, 'behavior', 0.05, 0.1)
    thresholds = write_thresholds(tmp_path / 'thresholds.json', {
        'median_end_to_end_real_time_factor': {'minimum': 0.1, 'source': 'docs'},
    })
    summary = tmp_path / 'summary.md'
    summary.write_text('existing\n')
    monkeypatch.setenv('GITHUB_STEP_SUMMARY', str(summary))
    monkeypatch.setattr('sys.argv', ['check_benchmark.py', str(tmp_path), '--thresholds', str(thresholds),
                                     '--campaign-wall-seconds', '60'])

    assert BENCH.main() == 1
    report = json.loads((tmp_path / 'benchmark.json').read_text())
    assert report['failures']
    assert report['metrics']['episodes_per_minute']['value'] == 1.0
    assert summary.read_text().startswith('existing\n')
    assert 'Benchmark regression' in summary.read_text()


def test_main_passes_on_a_healthy_campaign_with_the_checked_in_thresholds(tmp_path, monkeypatch):
    write_attempt(tmp_path, 1, 1, 'behavior', 0.42, 0.66)
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr('sys.argv', ['check_benchmark.py', str(tmp_path), '--thresholds', str(THRESHOLDS)])

    assert BENCH.main() == 0


def test_main_rejects_an_invalid_thresholds_file(tmp_path, monkeypatch):
    thresholds = tmp_path / 'thresholds.json'
    thresholds.write_text('{"schema_version": 2, "metrics": {}}')
    monkeypatch.setattr('sys.argv', ['check_benchmark.py', str(tmp_path), '--thresholds', str(thresholds)])

    with pytest.raises(SystemExit) as error:
        BENCH.main()

    assert error.value.code == 2


def test_thresholds_file_rejects_duplicate_keys(tmp_path):
    path = tmp_path / 'thresholds.json'
    path.write_text('{"schema_version": 1, "schema_version": 1, "metrics": {}}')

    with pytest.raises(ValueError, match='duplicate'):
        BENCH.load_thresholds(path)


@pytest.mark.parametrize('rate', [float('nan'), float('inf'), 'fast', True])
def test_non_finite_or_non_numeric_measurements_are_rejected(tmp_path, rate):
    bundle = tmp_path / 'case-000001' / 'attempt-000001' / 'bundle'
    bundle.mkdir(parents=True)
    (bundle / 'result.json').write_text(json.dumps({'classification': 'behavior', 'real_time_factor': rate}))

    with pytest.raises(ValueError):
        BENCH.measure_campaign(tmp_path, wall_seconds=None)


def test_malformed_replay_counts_are_rejected(tmp_path):
    (tmp_path / 'analysis.json').write_text(json.dumps({'corpus': [{'valid_replays': 3.5,
                                                                     'matching_replays': 1}]}))

    with pytest.raises(ValueError):
        BENCH.measure_campaign(tmp_path, wall_seconds=None)


@pytest.mark.parametrize('wall', ['0', '-5', 'nan', 'inf'])
def test_main_rejects_invalid_campaign_wall_time(tmp_path, monkeypatch, wall):
    monkeypatch.setattr('sys.argv', ['check_benchmark.py', str(tmp_path), '--thresholds', str(THRESHOLDS),
                                     '--campaign-wall-seconds', wall])

    with pytest.raises(SystemExit) as error:
        BENCH.main()

    assert error.value.code == 2


def test_main_exits_2_on_an_empty_thresholds_file(tmp_path, monkeypatch):
    thresholds = write_thresholds(tmp_path / 'thresholds.json', {})
    monkeypatch.setattr('sys.argv', ['check_benchmark.py', str(tmp_path), '--thresholds', str(thresholds)])

    with pytest.raises(SystemExit) as error:
        BENCH.main()

    assert error.value.code == 2


def test_checked_in_thresholds_do_not_compare_diagnostic_agreement_with_another_population():
    entry = BENCH.load_thresholds(THRESHOLDS)['diagnostic_replay_agreement']

    assert 'reference' not in entry
