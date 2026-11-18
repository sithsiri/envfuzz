"""Property tests: submission validation rejects malformed, oversized, nested and out-of-root inputs."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from envfuzz.classroom import check_submission
from envfuzz.scenario import Scenario, save_scenario

PROPERTY = settings(max_examples=40, deadline=None, derandomize=True)
SIZE_LIMIT = 400
VALID = {"challenge": "lab", "team": "blue", "edits": []}
SUBMISSION_KEYS = {"version", "challenge", "team", "edits"}

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=10),
    lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=8), children,
                                                                       max_size=4),
    max_leaves=12,
)


@pytest.fixture(scope="module")
def challenge(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("challenge")
    save_scenario(Scenario(), root / "fixture.yaml")
    manifest = root / "challenge.yaml"
    manifest.write_text(
        "id: lab\ndifficulty: introductory\nscenario: fixture.yaml\nbaseline: baseline.json\n"
        f"max_submission_bytes: {SIZE_LIMIT}\n",
        encoding="utf-8",
    )
    return manifest


def _submit(manifest: Path, body: bytes):
    path = manifest.parent.parent / f"{manifest.parent.name}-submission.json"
    path.write_bytes(body)
    return check_submission(manifest, path)


@PROPERTY
@given(body=st.binary(max_size=SIZE_LIMIT))
def test_arbitrary_bytes_are_rejected_with_value_errors(challenge, body):
    # Arrange
    assume(body.strip() != json.dumps(VALID).encode())

    # Act / Assert
    with pytest.raises(ValueError):
        _submit(challenge, body)


@PROPERTY
@given(document=json_values)
def test_json_documents_without_the_submission_shape_are_rejected(challenge, document):
    # Arrange
    assume(not (isinstance(document, dict) and set(document) <= SUBMISSION_KEYS
                and {"challenge", "team", "edits"} <= set(document)))
    body = json.dumps(document).encode()
    assume(len(body) <= SIZE_LIMIT)

    # Act / Assert
    with pytest.raises(ValueError):
        _submit(challenge, body)


@PROPERTY
@given(key=st.text(min_size=1, max_size=12).filter(lambda key: key not in SUBMISSION_KEYS), value=json_values)
def test_unknown_top_level_fields_are_rejected(challenge, key, value):
    # Arrange
    body = json.dumps({**VALID, key: value}).encode()
    assume(len(body) <= SIZE_LIMIT)

    # Act / Assert
    with pytest.raises(ValueError):
        _submit(challenge, body)


@PROPERTY
@given(padding=st.integers(min_value=0, max_value=2 * SIZE_LIMIT))
def test_byte_limit_is_enforced_exactly(challenge, padding):
    # Arrange: whitespace padding keeps the JSON valid while growing the file.
    body = json.dumps(VALID).encode() + b" " * padding

    # Act / Assert
    if len(body) <= SIZE_LIMIT:
        assert _submit(challenge, body)[1].team == "blue"
    else:
        with pytest.raises(ValueError, match="byte limit"):
            _submit(challenge, body)


NESTING = {"list": ("[", "]"), "object": ('{"a":', "}")}
ENVELOPE = '{"challenge":"lab","team":"blue","edits":[%s]}'


@PROPERTY
@given(data=st.data(), kind=st.sampled_from(sorted(NESTING)))
def test_deeply_nested_edit_payloads_are_rejected_without_recursion_errors(challenge, data, kind):
    # Arrange: choose the deepest nesting that still fits the byte limit, so the rejection
    # must come from schema validation rather than from the size check.
    opener, closer = NESTING[kind]
    room = SIZE_LIMIT - len(ENVELOPE % "0")
    depth = data.draw(st.integers(min_value=1, max_value=room // (len(opener) + len(closer))))
    body = (ENVELOPE % (opener * depth + "0" + closer * depth)).encode()
    assert len(body) <= SIZE_LIMIT

    # Act / Assert
    with pytest.raises(ValueError) as rejected:
        _submit(challenge, body)
    assert "byte limit" not in str(rejected.value)


segments = st.lists(st.sampled_from(["..", ".", "sub", "nested"]), min_size=1, max_size=6)


@PROPERTY
@given(parts=segments, field=st.sampled_from(["scenario", "baseline", "oracle", "params", "profile"]),
       absolute=st.booleans())
def test_organizer_paths_outside_the_challenge_root_are_rejected(tmp_path_factory, parts, field, absolute):
    # Arrange
    root = tmp_path_factory.mktemp("escape") / "challenge"
    root.mkdir()
    save_scenario(Scenario(), root / "fixture.yaml")
    relative = PurePosixPath(*parts, "target.yaml")
    target = (root.parent / relative) if absolute else relative
    escapes = absolute or not (root / relative).resolve().is_relative_to(root.resolve())
    assume(escapes)
    fields = {"scenario": "fixture.yaml", "baseline": "baseline.json", field: str(target)}
    manifest = root / "challenge.yaml"
    manifest.write_text("id: lab\ndifficulty: introductory\n" + "".join(
        f"{key}: {json.dumps(value)}\n" for key, value in fields.items()), encoding="utf-8")
    submission = root.parent / "submission.json"
    submission.write_text(json.dumps(VALID), encoding="utf-8")

    # Act / Assert
    with pytest.raises(ValueError, match="inside the challenge directory"):
        check_submission(manifest, submission)
