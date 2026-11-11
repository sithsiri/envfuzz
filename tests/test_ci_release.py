import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / 'docker'))
MODULE_PATH = ROOT / 'docker' / 'release_evidence.py'
SPEC = importlib.util.spec_from_file_location('release_evidence', MODULE_PATH)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
VERSIONS = {'envfuzz': '0.1.0', 'ros': 'jazzy', 'gazebo': 'harmonic', 'python': '3.12.3'}
SMOKE = {'status': 'completed', 'classification': 'behavior', 'real_time_factor': 0.4}
IMAGE = 'ghcr.io/example/envfuzz:v0.1.0'


def build(**overrides):
    arguments = {'versions': VERSIONS, 'smoke': SMOKE, 'tag': 'v0.1.0', 'revision': 'abc123',
                 'architecture': 'arm64', 'image': f'{IMAGE}-arm64', 'cpus': '2', 'memory': '3g',
                 'host_machine': 'aarch64'}
    return RELEASE.build_manifest(**{**arguments, **overrides})


def test_manifest_identifies_the_released_build_and_its_smoke_host():
    manifest = build()

    assert manifest['release'] == {'tag': 'v0.1.0', 'version': '0.1.0', 'revision': 'abc123',
                                   'image': f'{IMAGE}-arm64', 'architecture': 'arm64'}
    assert manifest['smoke'] == {'status': 'completed', 'classification': 'behavior',
                                 'real_time_factor': 0.4, 'host_machine': 'aarch64', 'native': True,
                                 'cpus': '2', 'memory': '3g'}
    assert manifest['components'] == VERSIONS


def test_manifest_records_an_emulated_smoke_host():
    assert build(host_machine='x86_64')['smoke']['native'] is False
    assert build(architecture='amd64', host_machine='x86_64')['smoke']['native'] is True


def test_manifest_accepts_a_tag_without_a_v_prefix():
    assert build(tag='0.1.0')['release']['version'] == '0.1.0'


def test_manifest_rejects_a_tag_that_does_not_match_the_packaged_version():
    with pytest.raises(ValueError, match='does not match'):
        build(tag='v0.2.0')


@pytest.mark.parametrize('smoke', [
    {'status': 'timeout', 'classification': 'behavior'},
    {'status': 'watchdog', 'classification': 'infrastructure'},
])
def test_manifest_rejects_a_failed_smoke_run(smoke):
    with pytest.raises(ValueError, match='smoke'):
        build(smoke=smoke)


def test_manifest_rejects_an_unsupported_architecture():
    with pytest.raises(ValueError, match='architecture'):
        build(architecture='riscv64')


def test_manifest_command_writes_the_manifest(tmp_path):
    (tmp_path / 'versions.json').write_text(json.dumps(VERSIONS))
    (tmp_path / 'result.json').write_text(json.dumps(SMOKE))
    output = tmp_path / 'manifest.json'

    code = RELEASE.main([
        'manifest', '--versions', str(tmp_path / 'versions.json'),
        '--smoke-result', str(tmp_path / 'result.json'), '--tag', 'v0.1.0', '--revision', 'abc123',
        '--architecture', 'amd64', '--image', f'{IMAGE}-amd64', '--cpus', '2', '--memory', '3g',
        '--output', str(output),
    ])

    assert code == 0
    assert json.loads(output.read_text())['release']['architecture'] == 'amd64'


def test_manifest_command_exits_with_usage_error_on_version_mismatch(tmp_path):
    (tmp_path / 'versions.json').write_text(json.dumps(VERSIONS))
    (tmp_path / 'result.json').write_text(json.dumps(SMOKE))

    with pytest.raises(SystemExit) as error:
        RELEASE.main([
            'manifest', '--versions', str(tmp_path / 'versions.json'),
            '--smoke-result', str(tmp_path / 'result.json'), '--tag', 'v9.9.9', '--revision', 'abc',
            '--architecture', 'amd64', '--image', IMAGE, '--cpus', '2', '--memory', '3g',
            '--output', str(tmp_path / 'manifest.json'),
        ])

    assert error.value.code == 2
    assert not (tmp_path / 'manifest.json').exists()


def manifests():
    return [build(architecture='amd64', image=f'{IMAGE}-amd64', host_machine='x86_64'), build()]


def test_notes_use_generated_notes_when_the_release_body_is_empty():
    notes = RELEASE.compose_notes('', '## What changed\n* Fix', manifests(), IMAGE)

    assert notes.startswith('## What changed\n* Fix')
    assert RELEASE.VERIFICATION_MARKER in notes
    assert f'`{IMAGE}`' in notes
    assert '| amd64 | completed | x86_64 (native) | 2 CPUs, 3g | 0.1.0 | jazzy | harmonic |' in notes
    assert '| arm64 | completed | aarch64 (native) | 2 CPUs, 3g | 0.1.0 | jazzy | harmonic |' in notes
    assert 'abc123' in notes


def test_notes_keep_an_author_written_body():
    notes = RELEASE.compose_notes('Hand-written notes', 'generated', manifests(), IMAGE)

    assert notes.startswith('Hand-written notes')
    assert 'generated' not in notes


def test_notes_replace_a_previous_verification_section_when_rerun():
    first = RELEASE.compose_notes('Body', '', manifests(), IMAGE)

    second = RELEASE.compose_notes(first, '', manifests(), IMAGE)

    assert second == first
    assert second.count(RELEASE.VERIFICATION_MARKER) == 1


def test_notes_command_writes_the_notes(tmp_path):
    current, generated = tmp_path / 'current.md', tmp_path / 'generated.md'
    current.write_text('')
    generated.write_text('generated notes')
    paths = []
    for manifest in manifests():
        path = tmp_path / f"{manifest['release']['architecture']}.json"
        path.write_text(json.dumps(manifest))
        paths += ['--manifest', str(path)]
    output = tmp_path / 'notes.md'

    code = RELEASE.main(['notes', '--current', str(current), '--generated', str(generated), *paths,
                         '--image', IMAGE, '--output', str(output)])

    assert code == 0
    assert output.read_text().startswith('generated notes')


@pytest.mark.parametrize('ros', ['unavailable', None])
def test_manifest_rejects_an_image_without_ros(ros):
    versions = {key: value for key, value in VERSIONS.items() if key != 'ros'}
    if ros is not None:
        versions['ros'] = ros

    with pytest.raises(ValueError, match='ROS'):
        build(versions=versions)


def test_manifest_requires_the_exact_pep440_version_for_prereleases():
    versions = {**VERSIONS, 'envfuzz': '0.2.0rc1'}

    assert build(versions=versions, tag='v0.2.0rc1')['release']['version'] == '0.2.0rc1'
    with pytest.raises(ValueError, match='does not match'):
        build(versions=versions, tag='v0.2.0-rc.1')


def test_notes_treat_a_null_release_body_as_empty():
    notes = RELEASE.compose_notes('null\n', 'generated', manifests(), IMAGE)

    assert notes.startswith('generated')


def test_notes_reject_duplicate_architecture_manifests():
    amd64 = manifests()[0]

    with pytest.raises(ValueError, match='architecture'):
        RELEASE.compose_notes('', 'generated', [amd64, amd64], IMAGE)


def test_notes_reject_a_missing_architecture():
    with pytest.raises(ValueError, match='architecture'):
        RELEASE.compose_notes('', 'generated', manifests()[:1], IMAGE)


def test_notes_reject_manifests_from_different_releases():
    amd64, arm64 = manifests()
    other = {**arm64, 'release': {**arm64['release'], 'tag': 'v0.0.9'}}

    with pytest.raises(ValueError, match='same release'):
        RELEASE.compose_notes('', 'generated', [amd64, other], IMAGE)


@pytest.mark.parametrize('content', ['[]', '{"release": {}}', '{"a": 1, "a": 2}', '{"x": NaN}'])
def test_notes_command_rejects_malformed_manifests_without_a_traceback(tmp_path, capsys, content):
    current, generated, bad = tmp_path / 'current.md', tmp_path / 'generated.md', tmp_path / 'bad.json'
    current.write_text('')
    generated.write_text('generated')
    bad.write_text(content)
    good = tmp_path / 'amd64.json'
    good.write_text(json.dumps(manifests()[0]))

    with pytest.raises(SystemExit) as error:
        RELEASE.main(['notes', '--current', str(current), '--generated', str(generated),
                      '--manifest', str(good), '--manifest', str(bad), '--image', IMAGE,
                      '--output', str(tmp_path / 'notes.md')])

    assert error.value.code == 2
    assert 'Traceback' not in capsys.readouterr().err


@pytest.mark.parametrize(('versions', 'smoke'), [([], SMOKE), (VERSIONS, [])])
def test_manifest_rejects_non_object_inputs(versions, smoke):
    with pytest.raises(ValueError, match='JSON objects'):
        build(versions=versions, smoke=smoke)
