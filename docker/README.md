# CPU image

The target runtime is Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic.
It intentionally does not install NVIDIA, X11, RViz, or desktop packages.
The local validation host did not provide Docker, so the image build remains an
opt-in integration check (`docker build -t envfuzz:jazzy .`).

