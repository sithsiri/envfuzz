from envfuzz.oracle import pose_error_events, restricted_events, score_events, stuck_events
from envfuzz.results import PoseSample
from envfuzz.scenario import Zone


def samples(points):
    return [PoseSample(sim_time=t, x=x, y=y) for t, x, y in points]


def test_stuck_and_pose_error_and_zone_events():
    trace = samples([(0, 0, 0), (1, 0.01, 0), (2, 0.01, 0)])
    assert stuck_events(trace, 0.1, 2)
    assert pose_error_events(trace, samples([(0, 1, 0), (1, 1, 0), (2, 1, 0)]), 0.5, 1)
    assert restricted_events(samples([(0, 0, 0), (1, 0.5, 0), (2, 2, 0)]),
                             [Zone(id="no-go", polygon=[(0, -1), (1, -1), (1, 1), (0, 1)])])
    assert score_events(stuck_events(trace, 0.1, 2))[0] == 1
