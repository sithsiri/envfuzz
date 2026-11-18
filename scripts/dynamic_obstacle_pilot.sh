#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
run_root="${1:-runs/dynamic-obstacle-pilot-20261111}"
mkdir -p "$run_root"

run_case() {
    local name="$1"
    local scenario="$2"
    shift 2
    printf 'Starting %s (%s)\n' "$name" "$scenario"
    docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
        run "$scenario" --profile examples/bumper-profile.yaml \
        --output "$run_root/$name" --timeout 35 --watchdog 150 "$@"
}

for pair in 01 02 03; do
    if ((10#$pair % 2)); then
        run_case "actor-control-$pair" examples/dynamic-obstacle-control.yaml
        run_case "actor-$pair" examples/scripted-actor-crossing.yaml
        run_case "timed-control-$pair" examples/dynamic-obstacle-control.yaml
        run_case "timed-$pair" examples/timed-obstacle-crossing.yaml
    else
        run_case "actor-$pair" examples/scripted-actor-crossing.yaml
        run_case "actor-control-$pair" examples/dynamic-obstacle-control.yaml
        run_case "timed-$pair" examples/timed-obstacle-crossing.yaml
        run_case "timed-control-$pair" examples/dynamic-obstacle-control.yaml
    fi
done

printf 'Three control/actor/timed-move comparisons completed in %s\n' "$run_root"
