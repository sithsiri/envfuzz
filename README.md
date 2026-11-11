# EnvFuzz

EnvFuzz tests how a simulated indoor robot navigates when its surroundings
change. You can run one layout, replay a saved mission, or generate a seeded
batch of layouts to compare navigation outcomes. It uses a TurtleBot3 in Gazebo
Harmonic with ROS 2 Jazzy, Nav2 for navigation, and SLAM Toolbox for mapping and
localization. The Docker image runs headlessly on CPU.

## How a run works

1. **Describe the world.** A YAML scenario defines room bounds, the robot's
   starting pose, one or more goals, obstacles, and optional restricted zones.
   See the [example apartment](examples/scenario.yaml).
2. **Validate and build it.** EnvFuzz checks geometry and clearance, then
   compiles the scenario into a Gazebo SDF world. The same scenario produces
   the same scenario hash and world file.
3. **Drive the robot.** Gazebo simulates the robot and its LiDAR. Nav2 sends
   it toward each goal while EnvFuzz records the Gazebo ground-truth position
   and the SLAM estimate. The mission limit uses simulated time. A separate
   wall-clock watchdog catches startup hangs and stalled runs.
4. **Save and inspect the result.** Each episode gets a folder with the scenario,
   configuration, outcome, logs, and telemetry. Generate a top-down trajectory
   plot or rerun the mission from that folder.

## Run an example

From the repository root, with Docker running:

```sh
docker build -t envfuzz:jazzy .
mkdir -p runs
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  run examples/scenario.yaml --output runs/example
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  report runs/example
```

Open `runs/example/trajectory.svg` to see the obstacles, goal, ground-truth path,
and aligned SLAM estimate. To repeat the mission with its saved scenario and
Nav2 settings:

```sh
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  replay runs/example --output runs/example-replay
```

Use a new output directory for each live run or replay. The mount keeps the
results on your machine after Docker exits.

## Run an experiment

Use the same Docker invocation and mount from the example, replacing the
arguments after `envfuzz:jazzy` with one of these commands.

| Command | What it does |
| --- | --- |
| `baseline --n 10 --output runs/baseline` | Repeats the default mission and saves the measured durations and position-error threshold. |
| `fuzz --budget 20 --seed 42 --baseline runs/baseline/baseline.json` | Adds seeded obstacles to the default layout, simulates and scores 20 valid scenarios, and appends results to `runs/results.jsonl`. |
| `campaign examples/campaign.yaml --output runs/pilot` | Runs uniform and stratified sampling across the configured layout families. Saves the plan, run bundles, and `results.csv`. Repeat the command to resume. |

A scored run uses a baseline for the same room bounds, start, goals, restricted
zones, and Nav2 settings. EnvFuzz checks for stalls during unfinished missions,
sustained position error above the calibrated threshold, and robot footprint
entry into a restricted zone. The result records the detected events and uses
the highest tier's points as the score. A score of zero means none of the
configured detectors fired. Check the mission status to see whether the robot
reached its goal.

A campaign can also be generated with `--plan-only`. The
[example manifest](examples/campaign.yaml) selects apartments, corridors, and
open rooms. Uniform sampling draws geometry categories at random. Stratified
sampling distributes trials across combinations of room width, clutter count,
and goal distance.

## Read a result bundle

| File | What to look for |
| --- | --- |
| `result.json` | Mission status, failure classification, events, and the baseline score when requested. |
| `metrics.json` | Goals reached, path length, and position RMSE from time-matched poses. |
| `telemetry.jsonl` | Timestamped Gazebo ground truth and available SLAM estimates in world coordinates. |
| `scenario.json`, `world.sdf`, `nav2.yaml` | The exact inputs for replay and debugging. |
| `timings.json`, `versions.json`, `logs.txt` | Simulation and wall-clock timing, runtime versions, and diagnostic logs. |

Start with `result.json`. A mission timeout is a navigation outcome. A launch
failure or wall-clock watchdog is an infrastructure outcome, with diagnostic
details in `logs.txt`.

## Develop without Docker

With Python 3.10 or newer, install the package in your Python environment and
run the local checks:

```sh
python -m pip install -e '.[test]'
python -m pytest
envfuzz scenario validate examples/scenario.yaml
envfuzz run examples/scenario.yaml --smoke --output runs/contract-check
```

The smoke command checks scenario compilation and bundle writing with zero
simulation time.

- [Progress and verified successes](docs/progress.md) records the paused implementation state and measured results.
- [Quickstart](docs/quickstart.md) covers Docker and native Ubuntu setup.
- [CLI reference](docs/cli.md) lists commands and options.
- [Simulation assets](docs/simulation-assets.md) describes the robot and ROS topics.
- [Phase 1 verification](docs/phase-1-verification.md) records the tested environments and measured results.
- [OpenSpec changes](openspec/changes/) track project requirements and implementation tasks.
- [Phase 2 workflow](docs/phase-2.md) covers parallel runs, map diagnostics, and classroom evaluation.
- [Classroom guide](docs/classroom-guide.md) covers participant submissions and organizer verification.
- [CPU profiles](docs/phase-3-profiles.md) covers the default and compact robots.
