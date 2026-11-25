# Architecture

EnvFuzz separates deterministic scenario contracts and result analysis from the
optional ROS 2/Gazebo runtime. This keeps validation, compilation, scoring, and
campaign bookkeeping testable without a simulator while allowing live episodes
to exercise the full navigation stack.

```mermaid
flowchart LR
    subgraph Inputs["Inputs and entry points"]
        YAML["Scenario YAML"]
        Config["Nav2 / oracle / profile config"]
        CLI["envfuzz CLI"]
        CampaignYAML["Campaign YAML"]
    end

    subgraph Core["Simulator-free Python contracts"]
        Scenario["Scenario model<br/>load + validate"]
        Compile["Deterministic SDF compiler<br/>canonical JSON + hash"]
        Generate["Fuzzer / campaign sampler<br/>seeded candidates and plans"]
        Runner["Episode runner<br/>simulation timeout + wall watchdog"]
        Contract["Result contracts<br/>EpisodeResult + schemas"]
        Oracle["Oracle + scoring<br/>baseline calibration and detectors"]
        Evidence["Reports / analysis / review<br/>metrics, plots, corpus, replay"]
    end

    subgraph Runtime["Optional ROS 2 + Gazebo runtime"]
        Launch["envfuzz_sim headless launch"]
        Gazebo["Gazebo Harmonic<br/>compiled world + TurtleBot"]
        Bridge["ros_gz_bridge"]
        Nav["Nav2<br/>planner, controller, recovery"]
        Mapping["SLAM Toolbox / AMCL / RTAB-Map"]
        Mission["ROS mission node<br/>goals, sim-time telemetry"]
        Timeline["Timeline plugin<br/>scheduled environment edits"]
    end

    subgraph Artifacts["Durable artifacts"]
        Bundle["Episode bundle<br/>scenario.json, world.sdf, result.json"]
        Trace["Telemetry and diagnostics<br/>telemetry, maps, logs, metrics"]
        Campaign["Campaign directory<br/>plan, results.csv, case attempts"]
        Baseline["Baseline bundle"]
    end

    YAML --> CLI
    CampaignYAML --> CLI
    Config --> CLI
    CLI --> Scenario
    CLI --> Generate
    Scenario --> Compile
    Generate --> Scenario
    Generate --> Runner
    Compile --> Runner
    Runner --> Contract
    Runner --> Launch
    Config --> Launch
    Launch --> Gazebo
    Launch --> Bridge
    Launch --> Nav
    Launch --> Mapping
    Launch --> Mission
    Compile --> Gazebo
    Timeline --> Gazebo
    Gazebo <--> Bridge
    Bridge <--> Nav
    Bridge <--> Mapping
    Mission <--> Nav
    Mission --> Trace
    Gazebo --> Trace
    Mission --> Contract
    Contract --> Bundle
    Trace --> Bundle
    Bundle --> Oracle
    Baseline --> Oracle
    Oracle --> Contract
    Contract --> Evidence
    Bundle --> Evidence
    Generate --> Campaign
    Bundle --> Campaign
    Campaign --> Evidence
    Evidence -. replay original inputs .-> Runner

    classDef deterministic fill:#e8f3ff,stroke:#2463a6,color:#102a43
    classDef runtime fill:#fff3df,stroke:#b36b00,color:#4a2b00
    classDef artifact fill:#eaf7ea,stroke:#398439,color:#173b17
    class Scenario,Compile,Generate,Runner,Contract,Oracle,Evidence deterministic
    class Launch,Gazebo,Bridge,Nav,Mapping,Mission,Timeline runtime
    class Bundle,Trace,Campaign,Baseline artifact
```

## Main paths

- **Single episode:** `CLI` loads a scenario, `Scenario` validates it, and
  `Compile` produces the deterministic `world.sdf`. `Runner` either writes a
  simulator-free smoke bundle or starts the Gazebo path. The ROS mission node
  sends goals through Nav2, records simulation-time ground truth and estimates,
  and writes telemetry. The result bundle is then scored and rendered.
- **Fuzzing and campaigns:** `Generate` creates seeded valid scenarios or a
  frozen campaign plan. Each case uses the same episode path, while campaign
  indexing and attempt/worker bookkeeping preserve resumability and
  reproducibility.
- **Replay and review:** a saved bundle supplies the original scenario and
  runtime settings. Replay uses the same runner, and `Evidence` compares
  signatures, telemetry, maps, logs, and oracle events before a case is treated
  as reproducible evidence.

The episode deadline is based on **simulation time**. Wall-clock time is used
only by the runner/watchdog to detect startup or infrastructure stalls.

Before each Nav2 dispatch, the ROS mission converts the authored
scenario-world goal into the `map` frame using the startup map-to-robot
alignment, then checks it against the latest `/map` occupancy grid. The grid
origin is the lower-left corner of cell `(0, 0)`, rotated by the origin yaw.
Its covered area includes the lower edges and excludes the upper edges at
`origin + (width, height) * resolution`. The check reports map coverage only;
Nav2 can still fail to plan to an in-bounds goal.
