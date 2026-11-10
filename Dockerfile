FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=jazzy \
    GZ_VERSION=harmonic \
    DISPLAY=

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip python3-venv \
    ros-jazzy-ros-gz ros-jazzy-navigation2 ros-jazzy-nav2-bringup \
    ros-jazzy-slam-toolbox ros-jazzy-nav2-minimal-tb3-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/envfuzz
COPY . .
RUN python3 -m pip install --break-system-packages .
ENTRYPOINT ["envfuzz"]
