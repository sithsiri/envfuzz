"""Build per-architecture release manifests and compose verified release notes.

``manifest`` combines ``envfuzz doctor`` output with the release tag, source
revision, image reference and smoke-run host. It fails when the tag (minus a
leading ``v``) is not exactly the packaged PEP 440 envfuzz version, when the
image reports no ROS distribution, or when the smoke episode did not complete.

``notes`` keeps an author-written release body, falls back to GitHub's
generated notes when the body is empty, and appends one verification section
listing each architecture's smoke result and component versions. It needs
exactly one manifest per supported architecture, all from the same release.
"""

from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from ci_json import load_json

SCHEMA_VERSION = 1
MACHINE_ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
SUPPORTED_ARCHITECTURES = frozenset(MACHINE_ARCHITECTURES.values())
VERIFICATION_MARKER = "<!-- envfuzz-release-verification -->"
MISSING_ROS = frozenset({None, "", "unavailable"})
EMPTY_BODIES = frozenset({"", "null"})


def build_manifest(
    *, versions: dict, smoke: dict, tag: str, revision: str, architecture: str, image: str,
    cpus: str, memory: str, host_machine: str,
) -> dict:
    if not isinstance(versions, dict) or not isinstance(smoke, dict):
        raise ValueError("Versions and smoke result must be JSON objects")
    if architecture not in SUPPORTED_ARCHITECTURES:
        raise ValueError(f"Unsupported architecture {architecture}")
    version = tag.removeprefix("v")
    if versions.get("envfuzz") != version:
        raise ValueError(f"Release tag {tag} does not match packaged envfuzz {versions.get('envfuzz')}")
    if versions.get("ros") in MISSING_ROS:
        raise ValueError("The image reports no ROS distribution")
    if smoke.get("status") != "completed" or smoke.get("classification") != "behavior":
        raise ValueError(f"The {architecture} smoke run ended with {smoke.get('status')}")
    return {
        "schema_version": SCHEMA_VERSION,
        "release": {"tag": tag, "version": version, "revision": revision, "image": image,
                    "architecture": architecture},
        "smoke": {"status": smoke["status"], "classification": smoke["classification"],
                  "real_time_factor": smoke.get("real_time_factor"), "host_machine": host_machine,
                  "native": MACHINE_ARCHITECTURES.get(host_machine) == architecture,
                  "cpus": cpus, "memory": memory},
        "components": versions,
    }


def _verification_section(manifests: list[dict], image: str) -> str:
    lines = [
        VERIFICATION_MARKER, "## Verified images", "",
        f"Multi-architecture image: `{image}`", "",
        "| Architecture | Smoke | Smoke host | Limits | envfuzz | ROS | Gazebo |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for manifest in sorted(manifests, key=lambda item: item["release"]["architecture"]):
        release, smoke, components = manifest["release"], manifest["smoke"], manifest["components"]
        host = f"{smoke['host_machine']} ({'native' if smoke['native'] else 'emulated'})"
        lines.append(
            f"| {release['architecture']} | {smoke['status']} | {host} | {smoke['cpus']} CPUs, "
            f"{smoke['memory']} | {components.get('envfuzz')} | {components.get('ros')} | "
            f"{components.get('gazebo')} |"
        )
    revisions = sorted({manifest["release"]["revision"] for manifest in manifests})
    lines += ["", f"Source revision: {', '.join(revisions)}. Each architecture's component manifest "
              "is attached as a release asset."]
    return "\n".join(lines) + "\n"


def _validate_manifests(manifests: list[object]) -> None:
    for manifest in manifests:
        sections = [manifest.get(key) if isinstance(manifest, dict) else None
                    for key in ("release", "smoke", "components")]
        if not all(isinstance(section, dict) for section in sections):
            raise ValueError("Each manifest needs release, smoke and components objects")
    architectures = sorted(manifest["release"].get("architecture") for manifest in manifests)
    if architectures != sorted(SUPPORTED_ARCHITECTURES):
        raise ValueError(f"Expected one manifest per architecture {sorted(SUPPORTED_ARCHITECTURES)}, "
                         f"got {architectures}")
    if len({(manifest["release"].get("tag"), manifest["release"].get("revision"))
            for manifest in manifests}) != 1:
        raise ValueError("Manifests must come from the same release tag and revision")


def compose_notes(current: str, generated: str, manifests: list[dict], image: str) -> str:
    _validate_manifests(manifests)
    body = current.split(VERIFICATION_MARKER, 1)[0].strip()
    if body in EMPTY_BODIES:
        body = generated.strip()
    return f"{body}\n\n{_verification_section(manifests, image)}"


def _manifest_command(args: argparse.Namespace) -> None:
    manifest = build_manifest(
        versions=load_json(args.versions), smoke=load_json(args.smoke_result), tag=args.tag,
        revision=args.revision, architecture=args.architecture, image=args.image, cpus=args.cpus,
        memory=args.memory, host_machine=platform.machine(),
    )
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _notes_command(args: argparse.Namespace) -> None:
    notes = compose_notes(
        args.current.read_text(encoding="utf-8"), args.generated.read_text(encoding="utf-8"),
        [load_json(path) for path in args.manifest], args.image,
    )
    args.output.write_text(notes, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="Write one architecture's release manifest")
    for option in ("--tag", "--revision", "--architecture", "--image", "--cpus", "--memory"):
        manifest.add_argument(option, required=True)
    for option in ("--versions", "--smoke-result", "--output"):
        manifest.add_argument(option, type=Path, required=True)
    manifest.set_defaults(handler=_manifest_command)
    notes = commands.add_parser("notes", help="Compose release notes with a verification section")
    for option in ("--current", "--generated", "--output"):
        notes.add_argument(option, type=Path, required=True)
    notes.add_argument("--manifest", type=Path, action="append", required=True)
    notes.add_argument("--image", required=True)
    notes.set_defaults(handler=_notes_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
