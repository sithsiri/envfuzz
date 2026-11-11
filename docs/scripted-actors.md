# Scripted actors

A version 2 scenario can give an existing box or cylinder a path. Put its
initial pose in `objects` and reference its ID under `actors`. Each keyframe
sets its pose at a positive simulation time.

```yaml
actors:
  - object_id: walker
    keyframes:
      - at: 3
        pose: {x: 2, y: 1.5}
      - at: 4
        pose: {x: 2, y: 2.5}
```

The actor starts at the object's initial pose at time zero. Between
keyframes the compiler interpolates position and takes the shortest yaw arc.
It schedules updates at intervals of at most 0.1 simulated seconds and
0.1 meters of translation. The actor holds its last pose after the final
keyframe. Each scheduled pose is checked against the scenario's geometry and
validity rules. Each object can have one actor track or use the existing
timed-move list. The combined timeline is capped at 1,000 updates.

Run the example in the headless ROS and Gazebo environment.

```sh
envfuzz scenario validate examples/scripted-actor.yaml
envfuzz run examples/scripted-actor.yaml --output runs/scripted-actor
envfuzz replay runs/scripted-actor --output runs/scripted-actor-replay
```

The example uses a cylinder as a pedestrian-sized proxy. Gazebo updates its
visual and collision pose together. The run bundle records each requested
actor pose and its applied simulation timestamp in `events-applied.json`.
An unapplied entry has a null applied time, which can occur if the episode
ends before the scheduled update. The bundle also includes timestamped
LiDAR ranges and ray angles in `scans.jsonl`.
