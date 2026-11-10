# Quickstart

## Python development on macOS or Linux

```sh
python -m pip install -e '.[test]'
python -m pytest
envfuzz scenario validate examples/scenario.yaml
envfuzz run examples/scenario.yaml --smoke --output runs/contract-check
envfuzz campaign examples/campaign.yaml --plan-only --output runs/pilot
```

The smoke run checks scenario compilation and bundle creation. Its result
contains zero simulation time. The campaign plan fixes the generated scenarios
and can be prepared on a machine without ROS or Gazebo.

## Live missions with Docker

From the repository root, build the image and mount a directory for results.

```sh
docker build -t envfuzz:jazzy .
mkdir -p runs
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  run examples/scenario.yaml --output runs/example
docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
  replay runs/example --output runs/example-replay
```

Open the trajectory plot after running:

```sh
envfuzz report runs/example
```

The bundle stores the scenario, compiled world, Nav2 parameters, logs, goal
outcomes, simulation and wall timings, and timestamped poses. Position RMSE
uses only samples with a SLAM estimate at the ground-truth timestamp. A mission
timeout is measured in simulated seconds. The wall-clock watchdog covers
startup, mission execution, and teardown.

## Live missions on native Ubuntu

Install ROS 2 Jazzy, Gazebo Harmonic, Nav2, SLAM Toolbox, the Nav2 minimal
TurtleBot3 simulation, and colcon. Use the system Python or a virtual
environment based on it so the ROS Python extensions can be imported.

```sh
source /opt/ros/jazzy/setup.bash
colcon build --base-paths ros2_ws/src
source install/local_setup.bash
envfuzz run examples/scenario.yaml --output runs/example
make test-integration
```

## Baselines and campaigns

```sh
envfuzz baseline --n 10 --output runs/baseline
envfuzz fuzz --budget 20 --seed 42 --baseline runs/baseline/baseline.json
envfuzz campaign examples/campaign.yaml --output runs/pilot
```

Run those commands in the same ROS environment as the example mission. For
Docker, replace the arguments after `envfuzz:jazzy` in the example above.
The baseline records completed mission durations and each run's maximum paired
position error. It calculates the position-error threshold using
`max(0.1 m, mean + 3*population_stddev)`. Live scoring uses the calibrated
world bounds, start, goals, restricted zones, and Nav2 configuration.
Use the same campaign manifest
and output path to resume a stopped run. Completed behavior results are reused
and an infrastructure failure gets a new attempt directory.
