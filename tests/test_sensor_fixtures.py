import json
from hashlib import sha256
from pathlib import Path

from typer.testing import CliRunner

from envfuzz.cli import app

FIXTURES = Path(__file__).parents[1] / "examples" / "classroom" / "sensor-fixtures.json"


def test_offline_sensor_fixtures_use_oracle_tiers():
    result = CliRunner().invoke(app, ["classroom", "check-sensor-fixtures", str(FIXTURES)])
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["fixture_sha256"] == sha256(FIXTURES.read_bytes()).hexdigest()
    assert report["passed"]
    assert [row["observed_tiers"] for row in report["fixtures"]] == [[], ["T2"], []]


def test_fixture_mismatch_is_visible_to_ci(tmp_path):
    suite = json.loads(FIXTURES.read_bytes())
    suite["fixtures"][1]["expected_tiers"] = []
    path = tmp_path / "sensor-fixtures.json"
    path.write_text(json.dumps(suite), encoding="utf-8")

    result = CliRunner().invoke(app, ["classroom", "check-sensor-fixtures", str(path)])
    assert result.exit_code == 2
    assert not json.loads(result.stdout)["passed"]
