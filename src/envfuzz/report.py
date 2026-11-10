"""Render a run bundle as a standalone top-down trajectory plot."""

from __future__ import annotations

import json
import math
from pathlib import Path
from xml.etree import ElementTree as ET

from .results import EpisodeResult, PoseSample
from .scenario import load_scenario


def render_report(bundle: Path, output: Path) -> None:
    scenario = load_scenario(bundle / "scenario.json")
    result = EpisodeResult.model_validate_json((bundle / "result.json").read_text(encoding="utf-8"))
    telemetry = bundle / "telemetry.jsonl"
    if not telemetry.is_file():
        raise ValueError("This bundle has no recorded telemetry")
    truth, estimate = [], []
    with telemetry.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            truth.append(PoseSample.model_validate(row["ground_truth"]))
            if row["estimated"] is not None:
                estimate.append(PoseSample.model_validate(row["estimated"]))
    if not truth:
        raise ValueError("This bundle has no recorded ground-truth samples")
    bounds = scenario.world
    width = bounds["max_x"] - bounds["min_x"]
    height = bounds["max_y"] - bounds["min_y"]
    scale = min(640 / width, 520 / height)
    x_offset, y_offset = 60, 90

    def point(x, y):
        return x_offset + (x - bounds["min_x"]) * scale, y_offset + (bounds["max_y"] - y) * scale

    def points(positions):
        return " ".join(f"{x:.2f},{y:.2f}" for x, y in (point(*position) for position in positions))

    svg = ET.Element("svg", xmlns="http://www.w3.org/2000/svg", viewBox="0 0 760 690",
                     width="760", height="690", role="img")
    ET.SubElement(svg, "title").text = "EnvFuzz navigation trajectory"
    ET.SubElement(svg, "rect", width="760", height="690", fill="#ffffff")
    ET.SubElement(svg, "text", x="60", y="42", fill="#101828",
                  attrib={"font-size": "25", "font-family": "sans-serif"}).text = "Navigation trajectory"
    status = f"{result.status.replace('_', ' ').capitalize()} · {result.sim_duration:.2f} s simulation time"
    ET.SubElement(svg, "text", x="60", y="69", fill="#475467",
                  attrib={"font-size": "15", "font-family": "sans-serif"}).text = status
    ET.SubElement(svg, "rect", x=str(x_offset), y=str(y_offset), width=str(width * scale),
                  height=str(height * scale), fill="#f8fafc", stroke="#344054")
    for zone in scenario.restricted_zones:
        ET.SubElement(svg, "polygon", points=points(zone.polygon), fill="#fef0c7", stroke="#b54708")
    for obstacle in scenario.objects:
        x, y = point(obstacle.pose.x, obstacle.pose.y)
        if obstacle.shape == "cylinder":
            ET.SubElement(svg, "circle", cx=str(x), cy=str(y), r=str(obstacle.radius * scale),
                          fill="#98a2b3")
        else:
            w, h = obstacle.size.x * scale, obstacle.size.y * scale
            ET.SubElement(svg, "rect", x=str(x - w / 2), y=str(y - h / 2), width=str(w),
                          height=str(h), fill="#98a2b3",
                          transform=f"rotate({-math.degrees(obstacle.pose.yaw)} {x} {y})")
    for trace, color, dash in ((truth, "#175cd3", "none"), (estimate, "#b54708", "5 3")):
        if trace:
            ET.SubElement(svg, "polyline", points=points((pose.x, pose.y) for pose in trace),
                          fill="none", stroke=color, attrib={"stroke-width": "2.5", "stroke-dasharray": dash})
    for label, pose in [("Start", scenario.start), *[(f"Goal {i}", pose) for i, pose in enumerate(scenario.goals, 1)]]:
        x, y = point(pose.x, pose.y)
        ET.SubElement(svg, "circle", cx=str(x), cy=str(y), r="4", fill="#067647")
        ET.SubElement(svg, "text", x=str(x + 7), y=str(y - 7), fill="#065f46",
                      attrib={"font-size": "13", "font-family": "sans-serif"}).text = label
    for event in result.events:
        location = min(truth, key=lambda pose: abs(pose.sim_time - event.sim_start))
        x, y = point(location.x, location.y)
        marker = ET.SubElement(svg, "circle", cx=str(x), cy=str(y), r="4", fill="#b42318")
        ET.SubElement(marker, "title").text = event.kind
    for x, color, label in ((60, "#175cd3", "Gazebo ground truth"), (280, "#b54708", "Aligned SLAM estimate")):
        if x == 280 and not estimate:
            continue
        ET.SubElement(svg, "line", x1=str(x), x2=str(x + 24), y1="642", y2="642",
                      stroke=color, attrib={"stroke-width": "3"})
        ET.SubElement(svg, "text", x=str(x + 32), y="647", fill="#344054",
                      attrib={"font-size": "15", "font-family": "sans-serif"}).text = label
    ET.indent(svg, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(svg).write(output, encoding="utf-8", xml_declaration=True)
