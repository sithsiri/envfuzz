from envfuzz.results import EpisodeResult, write_result_bundle
from envfuzz.scenario import Scenario


def test_bundle_contains_required_files(tmp_path):
    write_result_bundle(tmp_path, Scenario(), "<sdf/>",
                        EpisodeResult(status="completed", classification="behavior"))
    for name in ("scenario.json", "world.sdf", "result.json", "versions.json", "timings.json", "logs.txt"):
        assert (tmp_path / name).exists()
