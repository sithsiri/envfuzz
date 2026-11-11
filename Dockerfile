FROM ros:jazzy-ros-base@sha256:8eed0a4669bbf43ee331878a4c39bd8ebddea8e3312a58b4fae8edb14526b617

ARG ROS_APT_MIRROR=https://repo.ros2.org/ubuntu/main

ENV DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=jazzy \
    GZ_VERSION=harmonic \
    DISPLAY= \
    LIBGL_ALWAYS_SOFTWARE=1

RUN sed -i 's@http://@https://@g' /etc/apt/sources.list.d/ubuntu.sources \
    && sed -i -e 's/^Types: deb deb-src$/Types: deb/' \
        -e "s@http://packages.ros.org/ros2/ubuntu@${ROS_APT_MIRROR}@" /usr/share/ros-apt-source/ros2.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 \
        install -y --no-install-recommends \
        python3-pip python3-venv python3-colcon-ros python3-setuptools python3-wheel make \
        libegl-mesa0 libgl1-mesa-dri \
        ros-jazzy-gz-sim-vendor=0.0.13-* \
        ros-jazzy-ros-gz-bridge=1.0.24-* ros-jazzy-ros-gz-sim=1.0.24-* \
        ros-jazzy-navigation2=1.3.13-* ros-jazzy-nav2-bringup=1.3.13-* \
        ros-jazzy-slam-toolbox=2.8.5-* ros-jazzy-nav2-minimal-tb3-sim=1.0.1-* \
        ros-jazzy-rtabmap-ros=0.23.13-* \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv --system-site-packages /opt/envfuzz-venv \
    && /opt/envfuzz-venv/bin/python -m pip install --no-cache-dir \
        pydantic==2.11.7 PyYAML==6.0.2 typer==0.16.1 pytest==8.3.5
ENV PATH="/opt/envfuzz-venv/bin:${PATH}"

WORKDIR /workspace/envfuzz
COPY . .
RUN python3 -m pip install --no-build-isolation --no-deps .
RUN bash -c '. /opt/ros/jazzy/setup.bash && colcon build --base-paths ros2_ws/src --install-base /opt/envfuzz-sim'
RUN bash -c '. /opt/ros/jazzy/setup.bash && mkdir -p /usr/share/envfuzz \
    && envfuzz doctor > /usr/share/envfuzz/versions.json'
ENTRYPOINT ["/bin/bash", "-c", "unset DISPLAY; source /opt/ros/jazzy/setup.bash && source /opt/envfuzz-sim/local_setup.bash && exec envfuzz \"$@\"", "envfuzz"]
