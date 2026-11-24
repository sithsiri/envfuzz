#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
source /opt/envfuzz-sim/local_setup.bash

if [[ "${1:-}" == "replay" && " $* " == *" --video "* ]]; then
    export ENVFUZZ_VIDEO_CONTAINER=1
    export ENVFUZZ_GAZEBO_GUI=1
    export DISPLAY=:99
    Xvfb "$DISPLAY" -screen 0 1280x720x24 +extension GLX -nolisten tcp >/tmp/envfuzz-xvfb.log 2>&1 &
    XVFB_PID=$!
    cleanup() {
        kill "$XVFB_PID" 2>/dev/null || true
        wait "$XVFB_PID" 2>/dev/null || true
    }
    trap cleanup EXIT INT TERM
    for _ in $(seq 1 100); do
        if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
            break
        fi
        sleep 0.1
    done
    if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        echo "Xvfb did not become ready on $DISPLAY; replaying headlessly without video." >&2
        unset DISPLAY ENVFUZZ_GAZEBO_GUI
        envfuzz "$@"
        exit $?
    fi
    set +e
    envfuzz "$@" &
    APP_PID=$!
    wait "$APP_PID"
    STATUS=$?
    set -e
    exit "$STATUS"
fi

exec envfuzz "$@"
