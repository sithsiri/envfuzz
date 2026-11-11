import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / 'docker' / 'check_replay.py'
SPEC = importlib.util.spec_from_file_location('check_replay', MODULE_PATH)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


def test_nightly_records_performance_and_required_replay_failures(tmp_path):
    (tmp_path / 'analysis.json').write_text(json.dumps({'corpus': [{
        'representative': 'case-000001/attempt-000001/bundle',
        'valid_replays': 17, 'reproduction_rate': 0,
    }]}))
    bundle = tmp_path / 'case-000001' / 'attempt-000001' / 'bundle'
    bundle.mkdir(parents=True)
    (bundle / 'result.json').write_text(json.dumps({
        'classification': 'behavior', 'real_time_factor': 0.4,
    }))
    result = CHECK.check_campaign(tmp_path, 0.1, {})
    assert not result['failures']
    assert result['median_end_to_end_real_time_factor'] == 0.4
    result = CHECK.check_campaign(tmp_path, 0.5, {'case-000001/attempt-000001/bundle': 0.95})
    assert len(result['failures']) == 2
    analysis = json.loads((tmp_path / 'analysis.json').read_text())
    analysis['corpus'][0].update(valid_replays=20, reproduction_rate=0.95)
    (tmp_path / 'analysis.json').write_text(json.dumps(analysis))
    assert not CHECK.check_campaign(tmp_path, 0.1, {'case-000001/attempt-000001/bundle': 0.95})['failures']
