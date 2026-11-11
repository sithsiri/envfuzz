import json
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from envfuzz.gazebo import run_gazebo
from envfuzz.profile import SimulationProfile
from envfuzz.scenario import Scenario, compile_sdf, load_scenario

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
CONTACT_SYSTEM = "./world/plugin[@name='gz::sim::systems::Contact']"


def test_contact_system_is_opt_in():
    scenario = Scenario()
    assert ET.fromstring(compile_sdf(scenario)).find(CONTACT_SYSTEM) is None
    profile = SimulationProfile(record_contacts=True)
    assert ET.fromstring(compile_sdf(scenario, profile)).find(CONTACT_SYSTEM) is not None
    assert "record_contacts" not in SimulationProfile().model_dump(exclude_none=True)


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("ENVFUZZ_GAZEBO_TEST") != "1", reason="requires ROS 2 and Gazebo")
def test_bumper_reports_timestamped_threshold_contact(tmp_path):
    scenario = load_scenario(EXAMPLES / "bumper-contact.yaml")
    bundle = tmp_path / "bumper"
    result = run_gazebo(scenario, bundle, timeout=15, watchdog=90,
                        profile_file=EXAMPLES / "bumper-profile.yaml")
    assert result.classification == "behavior", result.reason
    events = [json.loads(line) for line in (bundle / "contacts.jsonl").read_text().splitlines()]
    hits = [
        row for row in events
        if any("low_threshold" in collision["name"] for collision in row["collisions"])
    ]
    assert hits
    assert all(row["kind"] == "bumper_contact" and row["sim_time"] > 0 for row in hits)
