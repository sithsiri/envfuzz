import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from typer.testing import CliRunner

from envfuzz.cli import app
from envfuzz.materials import reference_rays
from envfuzz.scenario import Size, compile_sdf, load_scenario
from envfuzz.variants import Edit, EditBudget, apply_edits, seeded_material_edit

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "surface-reference.yaml"


@pytest.mark.parametrize(("material", "distance", "intensity"), [
    ("transparent", 2.97, 500), ("reflective", 1.97, 500), ("opaque", 1.97, 120), ("dark", None, 5),
])
def test_passive_reference_returns(material, distance, intensity):
    scenario = load_scenario(EXAMPLE)
    scenario.objects[0].material = material
    row, = reference_rays(scenario, scenario.start, (0,))
    assert row["range_m"] == (pytest.approx(distance, abs=0.001) if distance is not None else None)
    assert row["intensity"] == intensity


def test_thin_panel_only_intercepts_center_ray():
    scenario = load_scenario(EXAMPLE)
    scenario.objects[0].material = "opaque"
    scenario.objects[0].size = Size(x=0.06, y=0.03)
    center, edge = reference_rays(scenario, scenario.start, (0, 0.1))
    assert center["surface"] == "panel"
    assert edge["surface"] == "backing"


def test_surface_edits_are_versioned_and_budgeted():
    scenario = load_scenario(EXAMPLE)
    edit = Edit(operator="material", object_id="panel", material="reflective")
    with pytest.raises(ValueError, match="outside the budget"):
        apply_edits(scenario, [edit], EditBudget())
    budget = EditBudget(allowed=["material"], max_edits=1)
    seeded = seeded_material_edit(scenario, 42, "panel")
    assert seeded == seeded_material_edit(scenario, 42, "panel")
    assert seeded.material != scenario.objects[0].material
    assert apply_edits(scenario, [seeded], budget).objects[0].material == seeded.material
    edited = apply_edits(scenario, [edit], budget)
    assert edited.objects[0].material == "reflective"
    visual = ET.fromstring(compile_sdf(edited)).find("./world/model[@name='obstacle_panel']/link/visual")
    assert float(visual.findtext("laser_retro")) == 500
    edited.version = "1"
    with pytest.raises(ValueError, match="requires scenario version 2"):
        compile_sdf(edited)


def test_surface_probe_cli():
    outcome = CliRunner().invoke(app, ["surface-probe", str(EXAMPLE)])
    assert outcome.exit_code == 0, outcome.output
    assert json.loads(outcome.stdout)[2]["surface"] == "backing"
