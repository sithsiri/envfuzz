"""Simulator-free occupancy-grid coverage checks."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class MapBounds:
    """Occupancy-grid extent in its header frame, using half-open cell extents."""

    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float = 0.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("map dimensions must be positive")
        if not math.isfinite(self.resolution) or self.resolution <= 0:
            raise ValueError("map resolution must be finite and positive")
        if not all(math.isfinite(value) for value in (self.origin_x, self.origin_y, self.origin_yaw)):
            raise ValueError("map origin must be finite")

    @property
    def extent_x(self) -> float:
        return self.width * self.resolution

    @property
    def extent_y(self) -> float:
        return self.height * self.resolution

    def contains(self, x: float, y: float) -> bool:
        """Return whether a header-frame point lies in the grid's cell area.

        The origin is the lower-left corner of cell (0, 0), not its center.
        The lower edges are inclusive and the upper edges are exclusive.
        """
        if not math.isfinite(x) or not math.isfinite(y):
            return False
        dx, dy = x - self.origin_x, y - self.origin_y
        c, s = math.cos(self.origin_yaw), math.sin(self.origin_yaw)
        local_x, local_y = c * dx + s * dy, -s * dx + c * dy
        return 0.0 <= local_x < self.extent_x and 0.0 <= local_y < self.extent_y

    def as_dict(self) -> dict[str, float | int]:
        return {
            "width": self.width, "height": self.height, "resolution": self.resolution,
            "origin_x": self.origin_x, "origin_y": self.origin_y,
            "origin_yaw": self.origin_yaw,
            "extent_x": self.extent_x, "extent_y": self.extent_y,
            "edge_convention": "lower-inclusive-upper-exclusive",
        }
