#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
run_root="${1:-runs/lidar-degradation-pilot-20261111}"
mkdir -p "$run_root"

run_case() {
    local condition="$1"
    local profile_args=()
    if [[ "$condition" == degraded ]]; then
        profile_args=(--profile examples/lidar-degraded-profile.yaml)
    fi
    printf 'Starting LiDAR condition %s\n' "$condition"
    docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy \
        run examples/lidar-obstacle-pilot.yaml "${profile_args[@]}" \
        --output "$run_root/$condition-$2" --timeout 35 --watchdog 150
}

for repeat in 01 02 03; do
    if ((10#$repeat % 2)); then
        run_case nominal "$repeat"
        run_case degraded "$repeat"
    else
        run_case degraded "$repeat"
        run_case nominal "$repeat"
    fi
done

printf 'Three nominal/degraded LiDAR pairs completed in %s\n' "$run_root"
