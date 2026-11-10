# envfuzz: Environment Fuzzing Framework for Small Autonomous Robots

> Working name: `envfuzz` (rename freely).
> Audience: coding agents (and humans) implementing the project.
> Status: planning. Nothing in this file is implemented yet unless a ticket says otherwise.

---

## 0. How to use this file (read first)

1. Work tickets roughly in ID order within a phase. Each ticket lists `Depends on`; do not start a ticket before its dependencies are done unless told otherwise.
2. A ticket is **done** only when every item in its *Acceptance criteria* passes, the code has tests where the ticket asks for them, and docs are updated.
3. Items marked **[VERIFY]** are technical claims taken from web docs or memory. Confirm them against the installed versions before relying on them, and record what you found in `docs/adr/`.
4. Items marked **[HUMAN]** need a decision from the project owner. Use the stated default assumption and keep going; do not block.
5. Record every significant design decision as a short ADR in `docs/adr/NNNN-title.md` (context, decision, consequences).
6. Prefer small, reviewable commits. Never commit large binaries or run outputs (`runs/` is gitignored).
7. Keep everything runnable on a **CPU-only laptop with no GPU and no display**. If a change requires a GPU or X11, it must be optional and off by default.

---

## 1. Project summary

`envfuzz` is a framework for **fuzzing the physical environment** of small autonomous robots (robot vacuums are the reference robot) in simulation. It automatically generates and mutates environments (moving furniture and clutter, changing surfaces, adding obstacles, and so on) to find configurations that make the robot do something it normally would not: get stuck, mislocalize, build a wrong map, or enter a restricted area.

It must be usable in **capture-the-flag (CTF) competitions**: participants get a challenge, craft or search for environment modifications, and are scored by how severely they deviate the robot from its normal behavior. Sensor spoofing is a secondary modality: it should exist, but be harder than simply writing fake data to a topic.

### Origin

This project grows out of `github.com/sithsiri/LiDARSpoofing`, a fork of `o3de/RobotVacuumSample` (O3DE simulator + ROS 2 + Nav2 + `slam_toolbox`). That repo's own contribution is a ROS 2 Python package, `ros2_ws/src/lidar_spoofing`, containing the `phantom_wall_spoofer` node. It subscribes to `/scan`, overwrites ranges inside an angular window with a fake distance plus Gaussian noise, and publishes `/scan_spoofed`, which SLAM and Nav2 consume.

SLAM in that repo is **not custom code**. It is `slam_toolbox` launched through Nav2 with a launch file and params YAML. The same approach is used here.

### Decisions already made

| ID | Decision |
|----|----------|
| D1 | Primary goal is **physical-environment fuzzing**. Sensor spoofing is a secondary, harder modality (Phase 3). |
| D2 | CTF scoring is **severity-tiered deviation from normal behavior** (see section 4). |
| D3 | Participants have **arbitrary laptops with no GPU**. Everything must run headless on CPU in Docker. |
| D4 | **Simulation time is decoupled from wall-clock time.** The sim should run as fast as the machine allows, and results must not depend on host speed (see ticket P2-10). |
| D5 | **Gazebo (Harmonic) with ROS 2 Jazzy on Ubuntu 24.04** replaces O3DE. O3DE is dropped. |
| D6 | Start from a **new repository** that depends on `nav2_minimal_turtlebot_simulation` and `ros_gz`, not from the O3DE fork. Port only the spoofer concept later (P3-01). |

---

## 2. Tech stack and constraints

- OS/Runtime: Ubuntu 24.04 in Docker (amd64 required, arm64 desired).
- ROS 2 Jazzy; Gazebo Harmonic via `ros_gz` (Jazzy + Harmonic binaries come from packages.ros.org). **[VERIFY]**
- Navigation: Nav2 (`nav2_bringup`). SLAM: `slam_toolbox` (online async). Starting simulation assets: `nav2_minimal_tb3_sim` (TurtleBot3-style circular differential-drive robot with a 2D LiDAR, a good stand-in for a robot vacuum). Its `tb3_simulation_launch.py` already exposes `slam`, `use_sim_time` and a headless option. **[VERIFY]**
- Orchestration and tooling: Python 3.12, `pydantic` for schemas, `typer` for the CLI, SQLite for results, `pytest` for tests.
- Pin every version (apt packages, Docker base image digests, Python deps) and record them in each run's output.

### Technical notes (all **[VERIFY]**)

- **Headless Gazebo:** `gz sim -s -r --headless-rendering <world.sdf>` with `DISPLAY` unset, using OGRE2. Sources describe this path as EGL talking to a GPU. Whether sensor rendering works acceptably on CPU-only machines through Mesa software rendering (llvmpipe) is **unproven here**; see spike P1-02.
- **Unthrottled sim speed:** the `<physics>` element controls it. One example world sets `<real_time_factor>-1.0</real_time_factor>`, other sources say `real_time_update_rate` 0 means "as fast as possible". Confirm which works on Harmonic.
- **Sim time:** every ROS node must run with `use_sim_time:=true` and `/clock` must be bridged. Any node using wall-clock time breaks determinism.
- **Isolation between parallel runs:** Gazebo transport has a `GZ_PARTITION` env var and ROS 2 has `ROS_DOMAIN_ID`. Preferred design is one self-contained container per episode (sim + nav + oracle together), with these two variables set uniquely as defense in depth.
- **Runtime world edits:** Gazebo Sim exposes per-world services for entity create/remove and set-pose. `ros_gz_sim` provides a `create` executable. Confirm exact service names and message types for Harmonic.

---

## 3. Target architecture

```
envfuzz/                      # repo root
  AGENTS.md                   # short conventions for coding agents (create in P1-00)
  README.md                   # quickstart for CPU-only laptops
  docker/                     # Dockerfile(s), entrypoints
  docs/adr/                   # architecture decision records
  src/envfuzz/                # Python package
    scenario/                 # schema, compiler, validity checks, mutators
    runner/                   # single-episode runner, parallel scheduler
    oracle/                   # metric recorders, detectors, scoring
    search/                   # strategies (random, evolutionary, ...)
    ctf/                      # challenge defs, submission, evaluation, scoreboard
    cli.py                    # `envfuzz ...` commands
  ros2_ws/src/
    envfuzz_worlds/           # SDF worlds, models, restricted-zone metadata
    envfuzz_bringup/          # launch files, Nav2/SLAM params, bridges
    envfuzz_oracle_nodes/     # ROS-side recorders (ground truth, tf, map)
  scenarios/                  # example scenario + challenge files
  tests/                      # unit, integration, regression
  runs/                       # gitignored outputs
```

**Core loop (one episode):**

```
Scenario (YAML) -> validate -> compile to SDF world -> launch headless Gazebo
  -> spawn robot -> launch Nav2 + slam_toolbox (sim time)
  -> send mission goal(s) -> record ground truth + SLAM outputs
  -> terminate (goal done / sim-time timeout / watchdog)
  -> oracle computes events + score -> write result bundle -> clean up
```

**Design principle:** one episode = one self-contained container or process tree with no external state. That makes parallelism, retries and sandboxing straightforward.

---

## 4. Oracle and scoring model

The oracle detects **deviations from normal behavior**, measured against an **unfuzzed baseline** of the same world and mission. Anything the robot does even without fuzzing does not count as a finding (for example, a robot that sometimes gets stuck on its own).

Initial severity ladder (all values are config, not code constants):

| Tier | Event | Points | Detection idea |
|------|-------|--------|----------------|
| T1 | Robot stuck | 1 | Ground-truth displacement below epsilon over a sim-time window while the mission is incomplete, **and** the baseline completed the mission |
| T2 | Pose error | 3 | SLAM pose (map to base frame via tf, aligned to world frame) vs ground-truth pose exceeds a baseline-derived threshold for longer than a duration |
| T3 | Map error | 5 | Final SLAM occupancy grid vs ground-truth grid exceeds a baseline-derived difference threshold (phantom or missing obstacles) |
| T4 | Restricted-area entry | 10 | Ground-truth robot footprint enters a restricted-zone polygon defined in the world metadata |

Open points **[HUMAN]** (defaults in parentheses):
- Does a scenario score the **highest** tier reached or the **sum** of tiers? (Default: highest tier, all events still recorded.)
- Extra tiers to consider: collision with fragile or forbidden object, leaving map bounds, falling or cliff event, mission failure with high wall-time cost. (Default: none for Phase 1 and 2.)
- A minimality bonus (fewer or smaller edits score higher)? (Default: no, but the minimizer in P2-09 should make it possible to add later.)

---

## 5. Scenario format (v0 sketch)

Agents may refine this in P1-05, but should keep it declarative, versioned and JSON-Schema-validated.

```yaml
schema_version: 0
world: apartment_small            # base world in envfuzz_worlds
seed: 1234
robot: tb3_vacuum_like
mission:
  start: {x: 0.5, y: 0.5, yaw: 0.0}
  goals:
    - {x: 4.0, y: 2.5, yaw: 0.0}
  timeout_sim_s: 300
objects:
  - id: chair_1
    shape: box                    # box | cylinder | model:<name>
    size: [0.4, 0.4, 0.8]
    pose: {x: 2.0, y: 1.0, yaw: 0.3}
    static: true
  - id: lamp_1
    shape: cylinder
    size: [0.15, 0.15, 1.5]
    pose: {x: 3.1, y: 2.0, yaw: 0.0}
    static: true
events: []                        # Phase 2: timed runtime changes, e.g. move chair_1 at t=40s
```

---

# PHASE 1: Bare minimum (one headless episode end to end, CPU only)

**Phase goal:** a user on a GPU-less laptop can run `envfuzz fuzz --budget 20` and get back a list of randomly generated scenarios with an oracle verdict and score for each. Sequential execution is fine.

**Critical path:** P1-00, P1-01, P1-02, P1-03, P1-04, P1-05, P1-06, P1-09, P1-10, P1-13.

### P1-00: Repo bootstrap
- **Goal:** Create the new repository with the layout in section 3, a license (Apache-2.0 unless the owner says otherwise), `AGENTS.md`, `.gitignore` (including `runs/`), `pyproject.toml`, `Makefile` with `make test`, `make lint`, `make image`.
- **Acceptance criteria:**
  - `AGENTS.md` summarizes sections 0 and 7 of this file in under 60 lines.
  - ADR 0001 records decisions D1 to D6 and why O3DE was dropped.
  - ADR 0002 records that `sithsiri/LiDARSpoofing` is legacy reference only; the `lidar_spoofing` package is to be ported in P3-01.
  - `make test` runs (even with zero tests) and passes in a clean checkout.
- **Depends on:** none.

### P1-01: CPU-only headless Docker image
- **Goal:** One Dockerfile producing an image with Ubuntu 24.04, ROS 2 Jazzy, Gazebo Harmonic, `ros_gz`, `nav2_bringup`, `slam_toolbox`, `nav2_minimal_tb3_sim`, and the `envfuzz` package. No NVIDIA toolkit, no X11, no RViz in the base image.
- **Acceptance criteria:**
  - `docker build` works on a plain amd64 machine and the final image runs without `--gpus` or `DISPLAY`.
  - Image prints all pinned versions via `envfuzz doctor`.
  - Image size and build time are documented in the README.
- **Depends on:** P1-00.

### P1-02: SPIKE: LiDAR on a CPU-only machine (highest technical risk)
- **Goal:** Find out whether Gazebo's LiDAR sensor can run acceptably with no GPU, and pick the approach.
- **Questions to answer (write results in ADR 0003):**
  1. Does `gpu_lidar` with OGRE2 work headless using Mesa software rendering (llvmpipe/EGL) in the container? At what scan rate and ray count, and at what CPU cost?
  2. If not, can a custom CPU ray-casting approach work (a Gazebo system plugin that casts rays against world collision geometry and publishes a LaserScan), and what does it cost to build?
  3. If neither is workable, what is the fallback (for example, a lightweight 2D simulator as the fast tier, with Gazebo kept for fidelity)?
- **Acceptance criteria:**
  - A measured table: approach, scans per second (sim time) at 360 rays, CPU cores used, achieved real-time factor.
  - A clear recommendation. All later tickets assume the recommended approach.
  - Note any approach's implication for material effects (glass, mirrors, dark surfaces), which matter in P3-02.
- **Depends on:** P1-01.

### P1-03: Baseline world and robot
- **Goal:** A small apartment world (3 to 4 rooms, doorways, a few furniture primitives) built from boxes and cylinders only, plus a vacuum-like circular differential-drive robot with 2D LiDAR, odometry, and a ground-truth pose publisher. Start from `nav2_minimal_tb3_sim` assets.
- **Acceptance criteria:**
  - World is SDF, boxes and cylinders only (cheap to simulate, easy to generate).
  - World metadata file lists named **restricted zones** (polygons), the robot start pose, and default goals.
  - Ground-truth robot pose is available on a ROS topic.
  - License of all assets is compatible with redistribution; record it.
- **Depends on:** P1-01, P1-02.

### P1-04: Headless sim + Nav2 + SLAM bring-up with sim time
- **Goal:** One launch file that starts headless Gazebo (unthrottled), bridges `/clock`, `/scan`, `/odom`, `/tf`, `/cmd_vel`, and ground truth, then starts Nav2 with `slam_toolbox`, all with `use_sim_time:=true`.
- **Acceptance criteria:**
  - The robot reaches a goal in the unfuzzed world, headless, with no display.
  - The achieved real-time factor is measured and logged at the end of each run.
  - No node uses wall-clock time for logic (check by running at a different RTF and getting the same result).
  - Document how to reduce compute (SLAM and Nav2 params) for slow laptops.
- **Depends on:** P1-02, P1-03.

### P1-05: Scenario schema v0
- **Goal:** Pydantic models plus an exported JSON Schema for the scenario format in section 5; load, validate, and canonical-serialize (stable key order) so a scenario hash is reproducible.
- **Acceptance criteria:**
  - Invalid scenarios fail with clear messages.
  - `envfuzz scenario validate file.yaml` works.
  - Unit tests cover valid, invalid, and round-trip cases.
- **Depends on:** P1-00.

### P1-06: Scenario to world compiler
- **Goal:** Deterministically compile `(base world + scenario)` to a concrete SDF world file. Phase 1 handles **static placement at world creation only**.
- **Acceptance criteria:**
  - Same scenario + same seed produces byte-identical SDF.
  - Objects get collision and visual geometry sized per the scenario.
  - Unit tests check generated SDF against golden files.
- **Depends on:** P1-03, P1-05.

### P1-07: Validity checker v0
- **Goal:** Reject physically or logically invalid scenarios before simulating.
- **Acceptance criteria:**
  - Rejects: objects overlapping each other or walls, objects outside world bounds, objects within a clearance radius of the start pose or goals, objects placed inside restricted zones if the challenge forbids it.
  - Returns a list of specific violations.
  - Unit tests for each rule.
- **Depends on:** P1-05, P1-03.

### P1-08: (reserved: ground-truth occupancy grid; moved to P2-03)

### P1-09: Single-episode runner
- **Goal:** `envfuzz run scenario.yaml --out runs/<id>` runs the whole core loop in section 3.
- **Acceptance criteria:**
  - Terminates on goal reached, sim-time timeout, or a **wall-clock watchdog** (default 10 minutes, configurable).
  - Always tears down every child process and leaves no orphans, even after a crash or Ctrl-C.
  - Distinguishes **infrastructure failures** (sim crash, launch failure) from **robot behavior outcomes** in its exit status and result file.
  - Exit codes documented.
- **Depends on:** P1-04, P1-06, P1-07.

### P1-10: Oracle v0 (stuck, pose error, restricted-area entry)
- **Goal:** Record ground-truth pose, SLAM pose (tf), and mission status; compute the T1, T2 and T4 events from section 4 against baseline-derived thresholds.
- **Acceptance criteria:**
  - Detectors are separate, small classes behind a common interface (`Detector.update(sample)` / `Detector.events()`), configured by an `oracle.yaml`.
  - The SLAM-to-world frame alignment is handled explicitly (the SLAM map origin is not the world origin); document the method.
  - Unit tests drive each detector with **synthetic traces** (no simulator needed).
  - T1 only fires if the baseline did not get stuck in the same situation.
- **Depends on:** P1-04.

### P1-11: Baseline calibration
- **Goal:** `envfuzz baseline --n 10` runs the unfuzzed scenario N times and writes `baseline.json` with metric distributions and the derived oracle thresholds.
- **Acceptance criteria:**
  - Thresholds are explainable (for example, mean + k standard deviations, with a floor).
  - `envfuzz run` and `envfuzz fuzz` refuse to score without a baseline file, or warn loudly.
- **Depends on:** P1-09, P1-10.

### P1-12: Result bundle v0
- **Goal:** Standard output directory per run.
- **Acceptance criteria:**
  - Contains `scenario.yaml`, `world.sdf`, `result.json` (verdict, tiers hit, score, timings, achieved RTF), `versions.json`, `logs/`, and an optional rosbag (flag, off by default).
  - `result.json` has a documented, versioned schema.
- **Depends on:** P1-09, P1-10.

### P1-13: Random fuzzer v0
- **Goal:** `envfuzz fuzz --budget N --seed S` generates N random valid scenarios (random objects, random placements within constraints), runs them sequentially, appends results to `runs/results.jsonl`, and prints the top findings.
- **Acceptance criteria:**
  - Fully reproducible from the seed.
  - Invalid scenarios are discarded without counting against the simulation budget.
  - Prints a sorted summary table with score and tiers hit.
- **Depends on:** P1-09, P1-11, P1-12.

### P1-14: Phase 1 smoke tests
- **Goal:** Minimum tests so agents can verify their own work.
- **Acceptance criteria:**
  - Unit tests for schema, compiler, validity, and detectors run in under 30 seconds with no simulator.
  - One integration test (marked `@pytest.mark.integration`) runs a short episode in a tiny world inside the container.
  - `make test` runs unit tests; `make test-integration` runs the integration test.
- **Depends on:** P1-06, P1-07, P1-10, P1-09.

### P1-15: Quickstart documentation
- **Goal:** README that gets a participant from zero to a first fuzz run on a no-GPU laptop.
- **Acceptance criteria:** Steps are tested from a clean machine or VM; includes troubleshooting for Docker permissions and low RAM.
- **Depends on:** P1-13.

---

# PHASE 2: Full functionality (parallel, smarter search, CTF-ready)

**Phase goal:** run many scenarios in parallel with reproducible results, search intelligently instead of purely randomly, and package everything so a CTF can be run end to end with authoritative scoring.

### P2-01: Parallel runner
- **Goal:** Run N episodes concurrently on one machine.
- **Acceptance criteria:**
  - `envfuzz fuzz --workers N` runs one self-contained container per episode with CPU and memory limits.
  - Each worker has a unique `ROS_DOMAIN_ID` and `GZ_PARTITION` (and no `--network=host` dependence).
  - Simple persistent job queue (SQLite or files) with retries for infrastructure failures only.
  - Crashed or hung workers are killed and requeued; infra failures are classified separately from findings.
  - A benchmark shows throughput scaling for N = 1, 2, 4, 8 on a reference machine.
- **Depends on:** Phase 1.

### P2-02: Results database and reports
- **Goal:** Replace JSONL with SQLite; add `envfuzz report`.
- **Acceptance criteria:** Queries by score, tier, scenario hash, strategy; a static HTML report with the top findings and aggregate stats.
- **Depends on:** P1-12.

### P2-03: Ground-truth occupancy grid and map-error metric (T3)
- **Goal:** Generate a ground-truth 2D occupancy grid from the compiled world (slice at LiDAR height) and compare it to the final SLAM map.
- **Acceptance criteria:**
  - Handles map-frame alignment between SLAM and world frames.
  - Metric choice documented (for example, occupied-cell IoU, or count of phantom and missing occupied cells), with a baseline-derived threshold.
  - Unit tests with synthetic grids.
- **Depends on:** P1-10, P1-11.

### P2-04: Reachability validity check
- **Goal:** Reject scenarios where no collision-free path exists from start to goals (A* on the ground-truth grid with robot-radius inflation), so participants cannot score by simply walling the robot in.
- **Acceptance criteria:** Configurable per challenge; unit tests on constructed layouts.
- **Depends on:** P2-03, P1-07.

### P2-05: Oracle v1
- **Goal:** Full config-driven severity ladder with T1 to T4, baseline-relative thresholds, a plugin interface for new detectors, and an oscillation/recovery-loop detector.
- **Acceptance criteria:**
  - Tier names, points and thresholds all come from `oracle.yaml`.
  - Each event stores a sim-time timestamp and evidence (for example, the max pose error observed).
  - Adding a new detector needs no change to core code.
- **Depends on:** P2-03.

### P2-06: Mutation operator library
- **Goal:** Reusable operators the search strategies and CTF players can apply.
- **Acceptance criteria:**
  - Operators: move, rotate, scale an object; add or remove clutter; open or close a door; change a floor friction patch (rug or wet floor, effective once P3-03 lands, with a stub until then).
  - Each operator declares its budget cost (for example, displacement in meters) and respects validity rules.
  - Operators are seeded and unit-tested, including property-based tests that mutated scenarios stay valid.
- **Depends on:** P1-05, P1-07.

### P2-07: Runtime (timed) world changes
- **Goal:** Support scenario `events` such as "at sim time 40 s move chair_1 to X" by calling Gazebo world services (create, remove, set-pose) during the episode. **[VERIFY]** service names.
- **Acceptance criteria:**
  - Events execute at the requested sim time, independent of wall-clock speed.
  - Schema v1 bumps the version and keeps v0 scenarios loadable.
  - An integration test moves an object mid-episode and asserts the LiDAR output changes.
- **Depends on:** P1-09, P2-06.

### P2-08: Search strategies and corpus
- **Goal:** A `Strategy` interface (`propose() -> Scenario`, `observe(result)`) with implementations: random (from Phase 1), hill-climbing / (1+lambda) evolution, and a genetic algorithm using the score as fitness.
- **Acceptance criteria:**
  - Strategies are selectable via `--strategy`.
  - A corpus of interesting scenarios is kept and mutated; findings are deduplicated by a behavioral signature (tiers hit plus coarse location of the event).
  - A comparison script shows that guided strategies beat random search on a benchmark challenge within the same simulation budget (or documents honestly that they do not).
- **Depends on:** P2-01, P2-05, P2-06.

### P2-09: Scenario minimizer and flake check
- **Goal:** Shrink a finding with delta debugging (remove objects, reduce displacements) while the same tier still triggers; re-run each candidate K times.
- **Acceptance criteria:**
  - `envfuzz minimize <run>` outputs a smaller scenario and the reproduction rate.
  - Reproduction rate is stored with the finding.
- **Depends on:** P2-01, P2-05.

### P2-10: Determinism, replay, and host-speed independence (high priority for CTF fairness)
- **Goal:** Make results independent of how fast the participant's laptop is.
- **Background:** Gazebo is deterministic in sim time, but Nav2 and SLAM compute in wall time. On a slow or loaded machine the navigation stack effectively reacts "later" in sim time, which can change outcomes. A CTF must not reward fast hardware.
- **Acceptance criteria:**
  - Spike: evaluate **lock-step** operation (step the simulation a fixed number of iterations, wait until the nav stack has processed the data, repeat) and/or throttling the sim to match the slowest node. Document the result in an ADR.
  - `envfuzz replay <run>` reruns a result bundle.
  - Measured **result stability**: the same scenario run on two different machines (or under CPU stress) yields the same tiers hit at an agreed rate (target to be set **[HUMAN]**, default >= 95%).
- **Depends on:** P1-04, P2-01.

### P2-11: Performance tuning
- **Goal:** Maximize simulated-seconds per wall-second on CPU.
- **Acceptance criteria:**
  - Profile and tune physics step size, sensor rate and ray count, SLAM and Nav2 params; document the trade-offs against result stability from P2-10.
  - Report achieved RTF on a reference laptop (default target: at least 5x real time, tunable **[HUMAN]**).
- **Depends on:** P2-10.

### P2-12: CTF challenge packaging
- **Goal:** Define a challenge as data.
- **Acceptance criteria:**
  - `challenge.yaml` specifies: world, robot, mission, oracle config and points, **allowed mutation operators**, **budgets** (max number of objects added or moved, max displacement, forbidden placement regions), time limits.
  - Players run `envfuzz ctf check my_scenario.yaml` for local validation and an advisory local score.
  - A submission bundle format (scenario file plus metadata, no executable code) is defined and size-limited.
- **Depends on:** P2-05, P2-04, P2-06.

### P2-13: Authoritative evaluation and scoreboard
- **Goal:** Organizers re-run submissions on trusted infrastructure; local scores are advisory only.
- **Acceptance criteria:**
  - Submissions are treated as **untrusted input**: strict schema validation, size and object-count limits, no arbitrary code, run in containers with no network, read-only filesystem, resource limits, and wall-clock timeouts.
  - Verified score and tiers are stored; a scoreboard (static HTML or JSON) is generated.
  - Re-evaluation is repeated K times to resist flaky scores (policy documented, for example, minimum tier reached in majority of runs).
- **Depends on:** P2-12, P2-10, P2-01.

### P2-14: Visualization and debugging tools
- **Goal:** Make findings understandable without a GPU.
- **Acceptance criteria:**
  - Top-down 2D rendering (matplotlib or SVG, no GPU) of the scenario, ground-truth path, SLAM-estimated path, SLAM map vs ground-truth map, and event markers.
  - `envfuzz show <run>` writes a PNG or HTML.
  - Optional: animated replay from recorded data.
- **Depends on:** P2-03, P1-12.

### P2-15: More worlds and difficulty tiers
- **Goal:** At least three base worlds with restricted zones and different layouts (open plan, narrow corridors, many rooms), each with calibrated baselines.
- **Acceptance criteria:** Each world has a baseline file, a documented difficulty, and at least one known solving scenario for organizers (a reference solution), checked in a hidden or organizer-only location.
- **Depends on:** P2-12.

---

# PHASE 3: More modalities and hardening (including full test coverage)

**Phase goal:** add the sensor-spoofing modality in a way that is harder than writing to a topic, broaden the physical effects and sensors, support more robot and SLAM variants, scale beyond one machine, and bring the project to a well-tested, CI-backed state.

### P3-01: Sensor-spoofing modality (emitter model)
- **Goal:** Replace "write any ranges you like to a topic" with a physically motivated attacker.
- **Design sketch:**
  - The attacker is an **in-simulation emitter** with a pose, aim direction, beam width/FOV, maximum range, power budget, duty cycle, and latency.
  - Injection happens **inside the sensing pipeline** (sensor plugin or a scan-processing stage that participants cannot address directly); players only control emitter parameters and placement within challenge limits.
  - Spoofed returns appear only for rays the emitter can plausibly affect, and are constrained by timing and power.
  - Difficulty tiers (for example, emitter placement limited to outside the apartment vs inside; knowledge of the robot's scan timing or not).
- **Acceptance criteria:**
  - Direct publishing to the sensor topics is **not available** in CTF mode (enforced in the container design, not just by convention).
  - The legacy `phantom_wall_spoofer` is ported as a **debug/reference mode only**, excluded from CTF scoring.
  - Spoofing events are scored by the same oracle tiers (T1 to T4).
  - Documented threat model and what a participant can and cannot control.
- **Depends on:** P2-12, P2-05.

### P3-02: LiDAR material and physics realism
- **Goal:** Model how surfaces affect LiDAR: glass and transparent surfaces, mirrors, very dark/absorbing materials, thin objects, and reflectivity-dependent dropouts.
- **Acceptance criteria:**
  - Material properties are part of the scenario schema and available as mutation operators.
  - Implementation approach follows the P1-02 ADR (custom ray-casting plugin or scan post-processing).
  - Tests demonstrate each effect on synthetic scans.
- **Depends on:** P1-02, P2-06.

### P3-03: Additional sensors and floor physics
- **Goal:** Broaden what can be fuzzed.
- **Acceptance criteria (each is its own sub-ticket):**
  - Bumper/contact sensor events.
  - Cliff sensors with drop geometry (stairs, ledges).
  - IMU and **wheel-odometry slip** from floor friction patches (rugs, wet floors, thresholds).
  - Camera / visual SLAM as an **optional, GPU-flagged** modality; never required for participants.
- **Depends on:** P2-06, P1-02.

### P3-04: Dynamic actors
- **Goal:** Moving people or pets as scenario events (scripted trajectories).
- **Acceptance criteria:** Actors are declared in the scenario, move deterministically in sim time, and appear in LiDAR; validity rules extend to actors.
- **Depends on:** P2-07.

### P3-05: Pluggable SLAM and navigation backends
- **Goal:** Make the stack under test a parameter.
- **Acceptance criteria:**
  - Support at least `slam_toolbox` (default), one alternative SLAM (for example Cartographer), and localization against a prebuilt map (AMCL).
  - Backend and params are part of the challenge definition; baselines are per backend.
  - A comparison report shows which backend is more robust to a fixed set of findings.
- **Depends on:** P2-11.

### P3-06: Additional robot models and sensor parameters
- **Goal:** A more realistic vacuum model (low profile, bumper, realistic LiDAR FOV, rate, noise) plus parameter variants.
- **Acceptance criteria:** Robot definitions are data files; each has its own baseline; CTF difficulty can vary by robot.
- **Depends on:** P3-05.

### P3-07: Advanced search
- **Goal:** Improve efficiency of finding high-severity deviations.
- **Acceptance criteria (optional, evidence-driven):**
  - Novelty or coverage-style objectives, Bayesian optimization or surrogate models, multi-objective search (maximize severity, minimize edit size).
  - Benchmarks versus Phase 2 strategies with an honest report.
- **Depends on:** P2-08, P2-09.

### P3-08: Distributed execution
- **Goal:** Run across multiple machines.
- **Acceptance criteria:** A multi-host job queue (Redis or similar), worker agents that pull jobs, and optionally Kubernetes/batch job templates; the same result bundles and the same authoritative evaluation path as P2-13.
- **Depends on:** P2-01, P2-13.

### P3-09: Comprehensive test suite
- **Goal:** Confidence to change the code, including by agents.
- **Acceptance criteria:**
  - **Unit tests:** schema, compiler, validity rules, every mutator, every oracle detector and metric on synthetic traces, scoring logic, submission validators.
  - **Property-based tests (Hypothesis):** random mutation sequences keep scenarios valid; compilation is deterministic; schema round-trips.
  - **Integration tests:** headless mini-episodes in CI (CPU only), parallel runner with fake workers, timed events, spoofing emitter.
  - **Regression corpus:** a set of known findings (scenario + expected tiers) that must keep reproducing within a stated tolerance.
  - **Determinism tests:** replay stability from P2-10 enforced in CI.
  - **Security tests for the submission pipeline:** malformed, oversized, deeply nested, and path-traversal inputs; resource-exhaustion attempts; verify sandbox restrictions.
  - **Performance benchmarks** tracked over time with a regression alarm.
  - Coverage target set **[HUMAN]** (default 85% for `src/`).
- **Depends on:** most of Phase 2.

### P3-10: CI/CD and releases
- **Goal:** Automated builds and checks.
- **Acceptance criteria:**
  - GitHub Actions (or equivalent): lint, unit tests on every push; integration tests on CPU runners; scheduled nightly regression and benchmark.
  - Multi-arch (amd64 and arm64) image build and publishing to a registry with version tags.
  - Release notes generated; images carry a version manifest.
- **Depends on:** P3-09.

### P3-11: Documentation and CTF organizer guide
- **Goal:** Make it usable by people who did not write it.
- **Acceptance criteria:**
  - Participant handbook (what you can change, how scoring works, how to test locally).
  - Organizer guide (creating challenges, calibrating baselines, verifying solvability with reference solutions, running the evaluation server, fairness and anti-cheat notes).
  - Architecture overview and a "how to add a detector / mutator / strategy / sensor" guide.
- **Depends on:** P3-01, P2-13.

### P3-12: Cross-platform support
- **Goal:** Work on the laptops participants actually have.
- **Acceptance criteria:** Documented and tested paths for Apple Silicon (arm64 image), Windows via WSL2, and a low-RAM mode with reduced sim fidelity and documented limits.
- **Depends on:** P3-10.

---

## 6. Risks and spikes (summary)

| Risk | Where addressed | Notes |
|------|-----------------|-------|
| No GPU means LiDAR rendering may be too slow or impossible in Gazebo | P1-02 | Headless docs describe an EGL/GPU path; a software path is unverified. Fallbacks: custom CPU ray casting, or a fast 2D tier. |
| Results depend on laptop speed because nav stack runs on wall time | P2-10 | Needs lock-step or throttling; matters for CTF fairness. |
| Runtime world edits may be awkward in Gazebo | P2-07 | Verify services early; fall back to restarting episodes for static-only scenarios. |
| Flaky findings (not reproducible) | P2-09, P2-10, P2-13 | Reproduction rate stored; re-evaluation K times. |
| Participants cheat via local score forgery or malicious files | P2-13 | Authoritative re-run on organizer infra; scenario-only submissions; sandboxing. |
| Trivial "wall the robot in" exploits | P2-04, P2-12 | Reachability check and placement budgets. |

---

## 7. Conventions for agents

- Python: type hints everywhere, `ruff` for lint and format, `mypy` on `src/`. No wall-clock time in simulation logic; use sim time.
- Randomness: every random choice takes an explicit seed; never use global RNG state.
- ROS nodes: always `use_sim_time:=true`; never block on wall-clock sleeps in logic.
- Config over constants: thresholds, points, budgets and timeouts live in YAML, with schema validation.
- Results are append-only and versioned (`result.json` has a `schema_version`).
- Never trust submitted scenario files: validate with the schema first, enforce limits, and never `eval` or load arbitrary code from them.
- When something in section 2 marked **[VERIFY]** turns out to be wrong, update this file and add an ADR.
