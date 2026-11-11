#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
run_root="runs/keepout-pilot-repeats-20261111"
mkdir -p "$run_root"

for pair in 01 02 03 04 05; do
    if ((10#$pair % 2)); then
        first=control
        second=filtered
    else
        first=filtered
        second=control
    fi
    for condition in "$first" "$second"; do
        args=(run examples/keepout-pilot.yaml
            --profile examples/amcl-localization-profile.yaml
            --output "$run_root/$condition-$pair" --timeout 45 --watchdog 180)
        if [[ "$condition" == filtered ]]; then
            args+=(--keepout-filter)
        fi
        printf 'Starting pair %s, condition %s\n' "$pair" "$condition"
        docker run --rm -v "$PWD/runs:/workspace/envfuzz/runs" envfuzz:jazzy "${args[@]}"
    done
done

printf 'Five matched pairs completed in %s\n' "$run_root"
