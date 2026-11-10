# Design

## Context

The repository already writes deterministic plans, individual Gazebo run bundles, and a resumable CSV. The manual container runs confirmed robot motion and paired pose capture. Package downloads prevented a fresh image build.

## Goals

- Verify the public CLI in the packaged headless environment.
- Make outcome counts and discovery curves reproducible from preserved inputs.

## Decisions

- Use the frozen plan and run bundles as analysis inputs. The plan retains candidate order and bundles retain evidence across retries.
- Freeze the case definition before evaluation. Start with mission outcome, layout family, and terminal position binned on a one-meter world grid. Tune the definition during the pilot and publish the final rule with the report.
- Replay one representative per signature three times using its archived scenario and Nav2 settings. Report the count of matching signatures and the total number of valid replays.
- Use the sample campaign to measure CPU runtime, then fix the evaluation budget and seeds. Report simulated and wall-clock duration.

## Risks and Trade-offs

- Nearby behaviors can fall on different grid boundaries. Inspect representative trajectory plots during the pilot and record any sensitivity to the bin size.
- Startup failures can consume wall time and change effective throughput. Report retries and their wall time alongside the shared behavior-episode budget.
