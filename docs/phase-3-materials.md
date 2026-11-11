# Passive surface reference fixtures

Version 2 scenarios accept `material` on box and cylinder objects. The
allowed values are `opaque`, `transparent`, `reflective` and `dark`.
Thin objects use their existing dimensions. Omitted material fields leave
legacy scenarios and their compiled geometry unchanged. An organizer can
enable the `material` edit operator in the challenge's edit budget.
Material edits count against the existing edit limit and support seeded
selection. Both material changes and clutter with explicit materials
require that operator to be allowed.

```sh
envfuzz scenario validate examples/surface-reference.yaml
envfuzz surface-probe examples/surface-reference.yaml
python -m pytest tests/test_materials.py
```

The static reference probe evaluates five fixed planar rays from the
scenario's start pose, at 0.16 m elevation. The idealized model treats the
transparent preset as transmissive, the reflective preset as a stronger
return, and an opaque return below the synthetic intensity threshold of
10 as a dropout. A thin surface is detected only if the ray intersects it.
The fixture expects the center ray to return the front panel at 1.970 m
for opaque or reflective material, the backing panel at 2.970 m for
transparent material, and no reported range for the dark material. The
range tolerance is 0.001 m.

The compiler also sets Gazebo visual transparency and return intensity.
The Gazebo Harmonic experiment found that the native planar range still
reported the front surface even when it was visually transparent.
Transmission and dropouts are therefore reference observations produced by
the offline probe. Navigation continues to consume Gazebo's native scan.
The reflective preset models intensity only. It does not model
angle-dependent secondary reflections. [Live surface probe](phase-3-verification.md#passive-lidar-surface-probe)
