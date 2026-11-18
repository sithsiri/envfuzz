"""Property tests: oracle detector, metric and scoring invariants on synthetic traces."""

from __future__ import annotations

from collections import deque

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from envfuzz.maps import Grid, _reachable, compare_grids
from envfuzz.oracle import (
    calibrate, pose_error_events, restricted_events, score_events, stuck_events,
)
from envfuzz.oracle_config import OracleConfig
from envfuzz.results import EpisodeEvent, PoseSample
from envfuzz.scenario import Pose, Zone
from envfuzz.scoring import recovery_events

PROPERTY = settings(max_examples=50, deadline=None, derandomize=True)
TIERS = ["T1", "T2", "T3", "T4"]
SAMPLE_PERIOD_S = 0.1

offset = st.floats(min_value=-3, max_value=3, allow_nan=False, allow_infinity=False)
positive = st.floats(min_value=0.01, max_value=5, allow_nan=False, allow_infinity=False)
events = st.lists(st.builds(EpisodeEvent, tier=st.sampled_from(TIERS), kind=st.just("synthetic"),
                            sim_start=st.floats(0, 100)), max_size=8)
tier_points = st.dictionaries(st.sampled_from(TIERS), st.integers(min_value=0, max_value=20), min_size=1)


@st.composite
def trajectories(draw, min_size: int = 1, max_size: int = 40) -> list[PoseSample]:
    """Strictly time-ordered planar traces at a fixed sample period."""
    steps = draw(st.lists(st.tuples(offset, offset), min_size=min_size, max_size=max_size))
    return [PoseSample(sim_time=round(i * SAMPLE_PERIOD_S, 6), x=x, y=y) for i, (x, y) in enumerate(steps)]


@st.composite
def paired_traces(draw) -> tuple[list[PoseSample], list[PoseSample]]:
    truth = draw(trajectories(min_size=2))
    keep = draw(st.lists(st.booleans(), min_size=len(truth), max_size=len(truth)))
    noise = st.tuples(offset, offset)
    estimates = [sample.model_copy(update={"x": sample.x + dx, "y": sample.y + dy})
                 for sample, kept, (dx, dy) in zip(truth, keep, [draw(noise) for _ in truth]) if kept]
    return truth, estimates


@PROPERTY
@given(found=events, points=tier_points)
def test_score_is_highest_hit_tier_and_tiers_are_ordered_by_points(found, points):
    # Act
    score, tiers = score_events(found, points)

    # Assert
    assert score == max((points.get(event.tier, 0) for event in found), default=0)
    assert 0 <= score <= max(points.values())
    assert set(tiers) == {event.tier for event in found}
    ranked = [points.get(tier, 0) for tier in tiers]
    assert ranked == sorted(ranked, reverse=True)


@PROPERTY
@given(found=events, extra=events, points=tier_points)
def test_score_never_decreases_when_more_events_are_found(found, extra, points):
    # Act
    before, _ = score_events(found, points)
    after, tiers = score_events(found + extra, points)

    # Assert
    assert after >= before
    assert set(score_events(found, points)[1]) <= set(tiers)


@PROPERTY
@given(found=events)
def test_default_tiers_rank_restricted_area_above_map_pose_and_stall(found):
    # Arrange
    points = {tier: setting.points for tier, setting in OracleConfig().tiers.items()}

    # Act
    _, tiers = score_events(found, points)

    # Assert
    assert tiers == sorted({event.tier for event in found}, reverse=True)


@PROPERTY
@given(trace=trajectories(), low=positive, high=positive, window=st.floats(0.1, 2))
def test_stuck_detection_is_monotonic_in_displacement_tolerance(trace, low, high, window):
    # Arrange
    low, high = sorted((low, high))

    # Act
    strict = stuck_events(trace, epsilon=low, window=window)
    loose = stuck_events(trace, epsilon=high, window=window)

    # Assert
    assert len(strict) <= 1 and len(loose) <= 1
    if strict:
        assert loose
        assert strict[0].measured_value <= low
        assert strict[0].sim_end - strict[0].sim_start >= window
    assert stuck_events(trace, epsilon=high, window=window, mission_complete=True) == []
    assert stuck_events(trace, epsilon=high, window=window, baseline_complete=False) == []


@PROPERTY
@given(traces=paired_traces(), low=positive, high=positive, duration=st.floats(0, 1),
       max_gap=st.one_of(st.none(), st.floats(0.05, 0.5)))
def test_pose_error_detection_is_monotonic_in_threshold(traces, low, high, duration, max_gap):
    # Arrange
    truth, estimates = traces
    low, high = sorted((low, high))

    # Act
    sensitive = pose_error_events(truth, estimates, low, duration, max_gap)
    tolerant = pose_error_events(truth, estimates, high, duration, max_gap)

    # Assert
    if tolerant:
        assert sensitive
    for found, threshold in ((sensitive, low), (tolerant, high)):
        for event in found:
            assert event.tier == "T2"
            assert event.measured_value > threshold
            assert event.sim_end - event.sim_start >= duration


@PROPERTY
@given(trace=trajectories(), x=offset, y=offset, side=positive, radius=st.floats(0, 0.5))
def test_restricted_events_are_ordered_intervals_inside_the_trace(trace, x, y, side, radius):
    # Arrange
    zone = Zone(id="zone", polygon=[(x, y), (x + side, y), (x + side, y + side), (x, y + side)])

    # Act
    found = restricted_events(trace, [zone], radius=radius)
    widened = restricted_events(trace, [zone], radius=radius + 0.5)

    # Assert
    first, last = trace[0].sim_time, trace[-1].sim_time
    for event in found:
        assert (event.tier, event.kind, event.details) == ("T4", "restricted_area", {"zone": "zone"})
        assert first <= event.sim_start <= event.sim_end <= last
    starts = [event.sim_start for event in found]
    assert starts == sorted(starts)
    assert len(found) <= (len(trace) + 1) // 2
    assert bool(found) <= bool(widened)  # a larger footprint never hides an intrusion
    assert restricted_events(trace, []) == []


@PROPERTY
@given(gaps=st.lists(st.floats(0, 10), max_size=10), count=st.integers(1, 5),
       short=st.floats(0, 20), extra=st.floats(0, 20))
def test_recovery_loop_detection_is_monotonic_in_window(gaps, count, short, extra):
    # Arrange
    times = [sum(gaps[:index + 1]) for index in range(len(gaps))]
    recoveries = [{"sim_time": t} for t in times]

    # Act
    narrow = recovery_events(recoveries, count, short)
    wide = recovery_events(recoveries, count, short + extra)

    # Assert
    if narrow:
        assert wide
        assert narrow[0].sim_end - narrow[0].sim_start <= short
        assert narrow[0].measured_value == count
    if len(recoveries) < count:
        assert wide == []


@PROPERTY
@given(values=st.lists(st.floats(0, 10), min_size=1, max_size=10), floor=st.floats(0, 1))
def test_calibrated_threshold_covers_every_calibration_trial(values, floor):
    # Act
    threshold = calibrate(values, {"world": "w"}, floor)["thresholds"]["metric"]

    # Assert: Samuelson's inequality bounds max - mean by stdev * sqrt(n - 1) <= 3 stdev for n <= 10.
    assert threshold >= floor
    assert threshold >= max(values) - 1e-9


@st.composite
def grids(draw, width: int | None = None, height: int | None = None) -> Grid:
    width = width or draw(st.integers(1, 8))
    height = height or draw(st.integers(1, 8))
    data = draw(st.lists(st.sampled_from([-1, 0, 100]), min_size=width * height, max_size=width * height))
    return Grid(width=width, height=height, resolution=0.5, origin=Pose(x=0, y=0), data=data)


@PROPERTY
@given(data=st.data())
def test_map_comparison_metrics_are_bounded_and_zero_for_identical_maps(data):
    # Arrange
    reference = data.draw(grids())
    estimate = data.draw(grids(reference.width, reference.height))

    # Act
    comparison = compare_grids(reference, estimate)
    identical = compare_grids(reference, reference)

    # Assert
    assert 0.0 <= comparison["coverage"] <= 1.0
    if comparison["map_error"] is not None:
        assert 0.0 <= comparison["map_error"] <= 1.0
        assert comparison["map_error"] == 1 - comparison["occupied_iou"]
    assert comparison["compared_cells"] <= sum(value != -1 for value in reference.data)
    assert identical["map_error"] in (0.0, None)
    assert identical["missing_occupied"] == identical["phantom_occupied"] == 0


def _bfs_reachable(grid: Grid, start: tuple[int, int], goal: tuple[int, int]) -> bool:
    seen, queue = {start}, deque([start])
    while queue:
        x, y = queue.popleft()
        if (x, y) == goal:
            return True
        for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if neighbor not in seen and grid.at(*neighbor) == 0:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


@settings(max_examples=50, deadline=None, derandomize=True)
@given(data=st.data())
def test_footprint_search_agrees_with_breadth_first_reachability(data):
    # Arrange
    width, height = data.draw(st.integers(2, 12)), data.draw(st.integers(2, 12))
    cells = data.draw(st.lists(st.sampled_from([0, 0, 100]), min_size=width * height,
                               max_size=width * height))
    grid = Grid(width=width, height=height, resolution=0.2, origin=Pose(x=0, y=0), data=cells)
    free = [(i, j) for j in range(height) for i in range(width) if grid.at(i, j) == 0]
    assume(free)
    start, goal = data.draw(st.sampled_from(free)), data.draw(st.sampled_from(free))

    # Act / Assert
    assert _reachable(grid, start, goal) == _bfs_reachable(grid, start, goal)

