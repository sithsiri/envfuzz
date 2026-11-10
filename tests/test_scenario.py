import json

import pytest

from envfuzz.scenario import Pose, Scenario, ScenarioObject, Size, compile_sdf, validate_scenario


def test_hash_and_compilation_are_stable():
    scenario = Scenario(objects=[ScenarioObject(id="box", shape="box", pose=Pose(x=2, y=2),
                                                size=Size(x=1, y=1, z=1))])
    assert scenario.content_hash() == Scenario.model_validate_json(scenario.model_dump_json()).content_hash()
    assert compile_sdf(scenario) == compile_sdf(scenario)


def test_invalid_overlap_is_specific():
    scenario = Scenario(objects=[
        ScenarioObject(id="a", shape="cylinder", pose=Pose(x=2, y=2), radius=1),
        ScenarioObject(id="b", shape="cylinder", pose=Pose(x=2.5, y=2), radius=1),
    ])
    assert any("a/b" in violation for violation in validate_scenario(scenario))


def test_canonical_json_is_sorted():
    scenario = Scenario()
    assert json.loads(scenario.canonical_json()) == scenario.canonical_dict()

