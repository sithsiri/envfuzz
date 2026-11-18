"""Property tests: schema round-trips, deterministic compilation and budgeted edit sequences."""

from __future__ import annotations

import math
import random
from xml.etree import ElementTree as ET

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from envfuzz.fuzzer import candidates
from envfuzz.scenario import (
    Pose, Scenario, ScenarioObject, Size, compile_sdf, load_scenario, save_scenario, validate_scenario,
)
from envfuzz.variants import Edit, EditBudget, apply_edits, seeded_clutter_edit

PROPERTY = settings(max_examples=40, deadline=None, derandomize=True)
FAST_PROPERTY = settings(max_examples=50, deadline=None, derandomize=True)

# Obstacle slots are 1.5 m apart and at least 1.5 m from the default start (0, 0) and goal (2, 0),
# so any object up to 0.5 m across placed at a jittered slot keeps every clearance rule.
SLOTS = [(x, y) for x in (-3.5, -2.0, -0.5, 1.0, 2.5, 3.5) for y in (-3.5, -2.0, 2.0, 3.5)]
MATERIALS = ["opaque", "transparent", "reflective", "dark"]

coordinate = st.floats(min_value=-4.5, max_value=4.5, allow_nan=False, allow_infinity=False)
heading = st.floats(min_value=-math.pi, max_value=math.pi, allow_nan=False, allow_infinity=False)
extent = st.floats(min_value=0.05, max_value=0.5, allow_nan=False, allow_infinity=False)
object_id = st.from_regex(r"[A-Za-z0-9_-]{1,10}", fullmatch=True)


@st.composite
def scenario_objects(draw, ids, positions, version: str) -> list[ScenarioObject]:
    objects = []
    for identifier, (x, y) in zip(ids, positions):
        pose = Pose(x=x, y=y, yaw=draw(heading))
        material = draw(st.sampled_from([None, *MATERIALS])) if version == "2" else None
        if draw(st.booleans()):
            obj = ScenarioObject(id=identifier, shape="box", pose=pose, material=material,
                                 size=Size(x=draw(extent), y=draw(extent), z=draw(extent)))
        else:
            obj = ScenarioObject(id=identifier, shape="cylinder", pose=pose, material=material,
                                 radius=draw(extent) / 2, height=draw(extent))
        objects.append(obj)
    return objects


@st.composite
def schema_scenarios(draw) -> Scenario:
    """Any schema-valid scenario, including ones that break semantic validity rules."""
    version = draw(st.sampled_from(["1", "2"]))
    ids = draw(st.lists(object_id, max_size=5, unique=True))
    positions = [(draw(coordinate), draw(coordinate)) for _ in ids]
    data = {
        "version": version, "seed": draw(st.integers(min_value=0, max_value=2**31)),
        "start": Pose(x=draw(coordinate), y=draw(coordinate), yaw=draw(heading)),
        "goals": [Pose(x=draw(coordinate), y=draw(coordinate)) for _ in range(draw(st.integers(0, 3)))],
        "objects": draw(scenario_objects(ids, positions, version)),
        "challenge": draw(st.dictionaries(st.sampled_from(["difficulty", "notes"]),
                                          st.text(max_size=12), max_size=2)),
    }
    if version == "2" and ids and draw(st.booleans()):
        data["events"] = [{"at": draw(st.floats(0, 3600)), "object_id": draw(st.sampled_from(ids)),
                           "pose": {"x": draw(coordinate), "y": draw(coordinate)}}]
    if version == "2" and draw(st.booleans()):
        data["floor_patches"] = [{"min_x": 1.0, "max_x": 2.0, "min_y": 1.0, "max_y": 2.0,
                                  "friction": draw(st.floats(0, 2))}]
    return Scenario.model_validate(data)


@st.composite
def valid_scenarios(draw) -> Scenario:
    """Scenarios that satisfy every validity rule, built from non-conflicting obstacle slots."""
    version = draw(st.sampled_from(["1", "2"]))
    slots = draw(st.lists(st.sampled_from(SLOTS), max_size=6, unique=True))
    jitter = st.floats(min_value=-0.2, max_value=0.2, allow_nan=False)
    positions = [(x + draw(jitter), y + draw(jitter)) for x, y in slots]
    ids = [f"obj-{index}" for index in draw(st.permutations(range(len(slots))))]
    scenario = Scenario(version=version, objects=draw(scenario_objects(ids, positions, version)))
    assert validate_scenario(scenario) == []
    return scenario


@pytest.fixture(scope="module")
def scratch(tmp_path_factory):
    return tmp_path_factory.mktemp("properties")


@PROPERTY
@given(scenario=schema_scenarios())
def test_canonical_json_round_trips_to_an_equal_scenario(scenario):
    # Act
    restored = Scenario.model_validate_json(scenario.canonical_json())

    # Assert
    assert restored == scenario
    assert restored.canonical_json() == scenario.canonical_json()
    assert restored.content_hash() == scenario.content_hash()


@PROPERTY
@given(scenario=schema_scenarios())
def test_yaml_files_round_trip_to_an_equal_scenario(scratch, scenario):
    # Arrange
    path = scratch / "scenario.yaml"

    # Act
    save_scenario(scenario, path)
    restored = load_scenario(path)

    # Assert
    assert restored == scenario
    assert restored.content_hash() == scenario.content_hash()


@PROPERTY
@given(scenario=valid_scenarios(), seed=st.integers(min_value=0, max_value=2**32 - 1))
def test_compilation_is_byte_identical_for_equivalent_scenarios(scenario, seed):
    # Arrange
    shuffled = list(scenario.objects)
    random.Random(seed).shuffle(shuffled)
    reordered = scenario.model_copy(update={"objects": shuffled})
    reloaded = Scenario.model_validate_json(scenario.canonical_json())

    # Act
    first, second = compile_sdf(scenario), compile_sdf(scenario)

    # Assert
    assert first == second
    assert compile_sdf(reloaded) == first
    assert compile_sdf(reordered) == first
    models = {model.get("name") for model in ET.fromstring(first.split("\n", 1)[1]).iter("model")}
    assert {f"obstacle_{obj.id}" for obj in scenario.objects} <= models


@PROPERTY
@given(scenario=schema_scenarios())
def test_compiler_never_emits_sdf_for_invalid_scenarios(scenario):
    # Arrange
    violations = validate_scenario(scenario)
    assume(violations)

    # Act / Assert
    with pytest.raises(ValueError):
        compile_sdf(scenario)


def _edit_base() -> Scenario:
    return Scenario(version="2", objects=[
        ScenarioObject(id="chair", shape="box", pose=Pose(x=2.0, y=2.0), size=Size(x=0.4, y=0.4, z=0.6)),
        ScenarioObject(id="bin", shape="cylinder", pose=Pose(x=-2.0, y=-2.0), radius=0.2, height=0.8),
        ScenarioObject(id="shelf", shape="box", pose=Pose(x=-2.0, y=2.5), size=Size(x=1.0, y=0.3, z=1.5)),
    ])


EDIT_OPERATORS = ["move", "rotate", "scale", "add_clutter", "remove_clutter", "material"]


def _random_edits(base: Scenario, seed: int, count: int) -> list[Edit]:
    """A seeded sequence of local edits plus fresh clutter IDs (never reusing a base object ID)."""
    rng = random.Random(seed)
    poses = {obj.id: obj.pose for obj in base.objects}
    edits = []
    for index in range(count):
        operator = rng.choice(EDIT_OPERATORS) if poses else "add_clutter"
        target = rng.choice(sorted(poses)) if poses else ""
        if operator == "move":
            pose = poses[target]
            edits.append(Edit(operator="move", object_id=target, pose=Pose(
                x=pose.x + rng.uniform(-0.8, 0.8), y=pose.y + rng.uniform(-0.8, 0.8))))
        elif operator == "rotate":
            edits.append(Edit(operator="rotate", object_id=target, value=rng.uniform(-1, 1)))
        elif operator == "scale":
            edits.append(Edit(operator="scale", object_id=target, value=rng.uniform(0.7, 1.3)))
        elif operator == "material":
            edits.append(Edit(operator="material", object_id=target, material=rng.choice(MATERIALS)))
        elif operator == "remove_clutter":
            edits.append(Edit(operator="remove_clutter", object_id=target))
            poses = {key: value for key, value in poses.items() if key != target}
        else:
            edits.append(seeded_clutter_edit(base, rng.randrange(2**32), f"clutter-{index}"))
    return edits

budgets = st.builds(
    EditBudget,
    allowed=st.one_of(st.just(EDIT_OPERATORS),
                      st.lists(st.sampled_from(EDIT_OPERATORS), min_size=1, unique=True)),
    max_edits=st.integers(min_value=0, max_value=8),
    # Well-formed challenges never ship a base fixture that already exceeds its own object cap.
    max_objects=st.integers(min_value=len(_edit_base().objects), max_value=6),
    max_displacement_m=st.floats(min_value=0, max_value=6),
    max_rotation_rad=st.floats(min_value=0, max_value=4),
    max_scale_delta=st.floats(min_value=0, max_value=2),
)


GENEROUS_BUDGET = EditBudget(allowed=EDIT_OPERATORS, max_edits=8, max_objects=12,
                             max_displacement_m=6, max_rotation_rad=8, max_scale_delta=3)


def _check_edit_outcome(base: Scenario, edits: list[Edit], budget: EditBudget) -> Scenario | None:
    """Apply edits and assert the variant is valid and within budget; None when cleanly rejected."""
    snapshot = base.canonical_json()
    try:
        variant = apply_edits(base, edits, budget)
    except ValueError:
        variant = None
    assert base.canonical_json() == snapshot  # the base fixture is never mutated
    if variant is None:
        return None
    assert validate_scenario(variant) == []
    assert len(edits) <= budget.max_edits
    assert len(variant.objects) <= budget.max_objects
    assert {edit.operator for edit in edits} <= set(budget.allowed)
    before = {obj.id: obj.pose for obj in base.objects}
    moved = sum(math.hypot(obj.pose.x - before[obj.id].x, obj.pose.y - before[obj.id].y)
                for obj in variant.objects if obj.id in before)
    assert moved <= budget.max_displacement_m + 1e-9
    turned = sum(abs(obj.pose.yaw - before[obj.id].yaw) for obj in variant.objects if obj.id in before)
    assert turned <= budget.max_rotation_rad + 1e-9
    assert apply_edits(base, edits, budget).content_hash() == variant.content_hash()
    return variant


@FAST_PROPERTY
@given(seed=st.integers(min_value=0, max_value=2**32 - 1), count=st.integers(1, 8))
def test_seeded_edit_sequences_under_generous_budget_stay_valid(seed, count):
    # Arrange
    base = _edit_base()
    edits = _random_edits(base, seed, count)

    # Act
    variant = _check_edit_outcome(base, edits, GENEROUS_BUDGET)

    # Assert
    if variant is not None:
        assert variant.content_hash() != base.content_hash() or all(
            edit.operator == "material" for edit in edits)


@FAST_PROPERTY
@given(seed=st.integers(min_value=0, max_value=2**32 - 1), count=st.integers(0, 8), budget=budgets)
def test_seeded_edit_sequences_never_exceed_a_random_budget(seed, count, budget):
    # Arrange
    base = _edit_base()
    edits = _random_edits(base, seed, count)

    # Act / Assert (budget and validity invariants are checked inside the helper)
    _check_edit_outcome(base, edits, budget)


def test_generous_budget_property_exercises_accepted_variants():
    # Arrange: guard against the property above silently degenerating into "always rejected".
    base = _edit_base()

    # Act
    accepted = sum(_check_edit_outcome(base, _random_edits(base, seed, 4), GENEROUS_BUDGET) is not None
                   for seed in range(40))

    # Assert
    assert accepted >= 10


@FAST_PROPERTY
@given(seed=st.integers(min_value=0, max_value=2**32 - 1), take=st.integers(1, 15))
def test_fuzzer_candidates_are_seed_deterministic_and_add_one_fresh_object(seed, take):
    # Arrange
    base = _edit_base()

    # Act
    first = [scenario for _, scenario in zip(range(take), candidates(seed, base))]
    second = [scenario for _, scenario in zip(range(take), candidates(seed, base))]

    # Assert
    assert [s.content_hash() for s in first] == [s.content_hash() for s in second]
    for scenario in first:
        assert scenario.seed == seed
        assert scenario.objects[:-1] == base.objects
        assert scenario.objects[-1].id not in {obj.id for obj in base.objects}
