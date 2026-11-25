"""Simulator-free tests for occupancy-grid coverage geometry."""

import math

import pytest

from envfuzz.map_bounds import MapBounds


def test_contains_points_inside_and_outside_grid():
    bounds = MapBounds(4, 3, 0.5, 1.0, -2.0)

    assert bounds.contains(1.0, -2.0)
    assert bounds.contains(2.999, -0.501)
    assert not bounds.contains(0.999, -1.0)
    assert not bounds.contains(1.5, -2.001)
    assert not bounds.contains(math.inf, 0.0)


def test_rotated_origin_is_inverted_before_bounds_check():
    bounds = MapBounds(4, 2, 0.5, 10.0, 20.0, math.pi / 2)

    assert bounds.contains(9.5, 20.5)
    assert not bounds.contains(9.5, 19.9)
    assert not bounds.contains(10.0, 20.0 + 2.0)


@pytest.mark.parametrize("point, expected", [
    ((0.0, 0.0), True),
    ((0.5, 0.25), True),
    ((2.0, 0.5), False),
    ((1.0, 1.0), False),
])
def test_cell_edges_are_lower_inclusive_and_upper_exclusive(point, expected):
    assert MapBounds(2, 1, 0.5, 0.0, 0.0).contains(*point) is expected


def test_rejects_invalid_map_geometry():
    with pytest.raises(ValueError, match="positive"):
        MapBounds(0, 2, 0.5, 0.0, 0.0)
