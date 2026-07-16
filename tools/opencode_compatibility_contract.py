# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — pinned OpenCode compatibility and drift contract
"""Validate the machine-readable OpenCode matrix and upstream release evidence."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.opencode_editor_workflow_contract import (  # noqa: E402
    EditorWorkflowError,
    assert_editor_workflow_contract,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "integrations" / "opencode" / "compatibility.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TAG_COMMIT = re.compile(r"[0-9a-f]{40}")
_PIN_TYPES = frozenset({"git-commit", "runtime-version", "sha256"})
_EXPECTED_ARTIFACTS = {
    "darwin-arm64": ("opencode-darwin-arm64.zip", "opencode"),
    "darwin-x64": ("opencode-darwin-x64.zip", "opencode"),
    "darwin-x64-baseline": ("opencode-darwin-x64-baseline.zip", "opencode"),
    "linux-arm64": ("opencode-linux-arm64.tar.gz", "opencode"),
    "linux-arm64-musl": ("opencode-linux-arm64-musl.tar.gz", "opencode"),
    "linux-x64": ("opencode-linux-x64.tar.gz", "opencode"),
    "linux-x64-baseline": ("opencode-linux-x64-baseline.tar.gz", "opencode"),
    "linux-x64-baseline-musl": (
        "opencode-linux-x64-baseline-musl.tar.gz",
        "opencode",
    ),
    "linux-x64-musl": ("opencode-linux-x64-musl.tar.gz", "opencode"),
    "windows-arm64": ("opencode-windows-arm64.zip", "opencode.exe"),
    "windows-x64": ("opencode-windows-x64.zip", "opencode.exe"),
    "windows-x64-baseline": ("opencode-windows-x64-baseline.zip", "opencode.exe"),
}
_EXPECTED_SMOKE_RUNNERS = {
    "darwin-arm64": "macos-15",
    "darwin-x64": "macos-15-intel",
    "linux-arm64": "ubuntu-24.04-arm",
    "linux-x64": "ubuntu-24.04",
    "windows-x64": "windows-2025",
}
_EXPECTED_COMPONENTS = frozenset(
    {
        "Neovim",
        "CodeCompanion.nvim",
        "plenary.nvim",
        "Emacs",
        "Shell Maker",
        "acp.el",
        "Agent Shell",
        "Zed",
        "IntelliJ IDEA",
        "JetBrains AI Assistant",
        "JetBrains Full Line Code Completion",
    }
)
_EXPECTED_CLIENTS = {
    "emacs": ("agent-shell", "0.59.1", "Agent Shell", ""),
    "jetbrains": ("JetBrains.IntelliJ IDEA", "2026.1.4", "IntelliJ IDEA", ""),
    "neovim": ("CodeCompanion.nvim", "1.0.0", "CodeCompanion.nvim", ""),
    "zed": (
        "zed",
        "1.10.3+stable.324.0c54c414d522234de7298039708ffe85a116892a",
        "Zed",
        "dev.zed.Zed",
    ),
}


class CompatibilityError(ValueError):
    """The compatibility contract or supplied upstream evidence is invalid."""


@dataclass(frozen=True)
class Artifact:
    """One immutable OpenCode CLI release artifact."""

    key: str
    name: str
    sha256: str
    binary: str
    runner: str
    smoke: bool


@dataclass(frozen=True)
class Component:
    """One editor-side ACP component and its immutable pin."""

    name: str
    version: str
    source: str
    pin_type: str
    pin: str


@dataclass(frozen=True)
class EditorClient:
    """One exact ACP client implementation identity emitted by an editor lane."""

    lane: str
    name: str
    version: str
    component: str
    x11_app_id: str


@dataclass(frozen=True)
class Compatibility:
    """The complete pinned OpenCode and editor compatibility matrix."""

    repository: str
    release_api: str
    version: str
    tag: str
    tag_commit: str
    artifacts: tuple[Artifact, ...]
    components: tuple[Component, ...]
    clients: tuple[EditorClient, ...]

    def artifact(self, key: str) -> Artifact:
        """Return the exactly named artifact or fail closed."""
        for artifact in self.artifacts:
            if artifact.key == key:
                return artifact
        raise CompatibilityError(f"unknown OpenCode compatibility platform: {key}")

    def client(self, lane: str) -> EditorClient:
        """Return the exactly named editor lane's ACP client identity."""
        for client in self.clients:
            if client.lane == lane:
                return client
        raise CompatibilityError(f"unknown OpenCode editor lane: {lane}")


def _object(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompatibilityError(f"{where} must be an object")
    return value


def _text(data: Mapping[str, Any], key: str, where: str, *, empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not empty and not value):
        raise CompatibilityError(f"{where}.{key} must be a string")
    if not value.isprintable():
        raise CompatibilityError(f"{where}.{key} must be printable")
    return value


def _exact_keys(data: Mapping[str, Any], expected: set[str], where: str) -> None:
    actual = {str(key) for key in data}
    if actual != expected:
        raise CompatibilityError(
            f"{where} fields differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def load_compatibility(path: Path = DEFAULT_MANIFEST) -> Compatibility:
    """Load and strictly validate one compatibility manifest."""
    try:
        root = _object(json.loads(path.read_text(encoding="utf-8")), "manifest")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"cannot read OpenCode compatibility manifest: {path}") from exc
    _exact_keys(
        root,
        {"schema_version", "upstream", "artifacts", "clients", "components"},
        "manifest",
    )
    if root.get("schema_version") != "synapse.opencode-compatibility.v2":
        raise CompatibilityError("unsupported OpenCode compatibility schema")

    upstream = _object(root.get("upstream"), "upstream")
    _exact_keys(
        upstream,
        {"repository", "release_api", "version", "tag", "tag_commit"},
        "upstream",
    )
    version = _text(upstream, "version", "upstream")
    tag = _text(upstream, "tag", "upstream")
    tag_commit = _text(upstream, "tag_commit", "upstream")
    repository = _text(upstream, "repository", "upstream")
    release_api = _text(upstream, "release_api", "upstream")
    if tag != f"v{version}" or _TAG_COMMIT.fullmatch(tag_commit) is None:
        raise CompatibilityError("OpenCode tag/version/commit pin is malformed")
    if repository != "anomalyco/opencode":
        raise CompatibilityError("OpenCode compatibility repository must be the official source")
    if release_api != f"https://api.github.com/repos/{repository}/releases":
        raise CompatibilityError("OpenCode release API must match the official repository")

    raw_artifacts = _object(root.get("artifacts"), "artifacts")
    artifacts: list[Artifact] = []
    for key, value in sorted(raw_artifacts.items(), key=lambda item: str(item[0])):
        name = str(key)
        data = _object(value, f"artifacts.{name}")
        _exact_keys(data, {"name", "sha256", "binary", "runner", "smoke"}, name)
        digest = _text(data, "sha256", name)
        smoke = data.get("smoke")
        runner = _text(data, "runner", name, empty=True)
        if _SHA256.fullmatch(digest) is None or not isinstance(smoke, bool):
            raise CompatibilityError(f"artifact {name} has invalid integrity metadata")
        if smoke != bool(runner):
            raise CompatibilityError(f"artifact {name} must pair smoke with an exact runner")
        artifact = Artifact(
            key=name,
            name=_text(data, "name", name),
            sha256=digest,
            binary=_text(data, "binary", name),
            runner=runner,
            smoke=smoke,
        )
        expected = _EXPECTED_ARTIFACTS.get(name)
        if expected is None or (artifact.name, artifact.binary) != expected:
            raise CompatibilityError(f"artifact {name} differs from the supported release layout")
        expected_runner = _EXPECTED_SMOKE_RUNNERS.get(name, "")
        if artifact.runner != expected_runner or artifact.smoke != bool(expected_runner):
            raise CompatibilityError(f"artifact {name} differs from the supported runner matrix")
        artifacts.append(artifact)
    if {item.key for item in artifacts} != set(_EXPECTED_ARTIFACTS):
        raise CompatibilityError("OpenCode compatibility manifest has incomplete platform coverage")
    if len({item.name for item in artifacts}) != len(artifacts):
        raise CompatibilityError("OpenCode artifact names must be unique")

    raw_components = root.get("components")
    if not isinstance(raw_components, list) or not raw_components:
        raise CompatibilityError("components must be a non-empty array")
    components: list[Component] = []
    for index, value in enumerate(raw_components):
        data = _object(value, f"components[{index}]")
        _exact_keys(data, {"name", "version", "source", "pin_type", "pin"}, "component")
        pin_type = _text(data, "pin_type", "component")
        pin = _text(data, "pin", "component")
        if pin_type not in _PIN_TYPES:
            raise CompatibilityError(f"component {index} has an unknown pin type")
        if pin_type == "sha256" and _SHA256.fullmatch(pin) is None:
            raise CompatibilityError(f"component {index} has an invalid SHA-256 pin")
        if pin_type == "git-commit" and _TAG_COMMIT.fullmatch(pin) is None:
            raise CompatibilityError(f"component {index} has an invalid commit pin")
        component = Component(
            name=_text(data, "name", "component"),
            version=_text(data, "version", "component"),
            source=_text(data, "source", "component"),
            pin_type=pin_type,
            pin=pin,
        )
        if pin_type == "runtime-version" and pin != component.version:
            raise CompatibilityError(f"component {index} runtime pin differs from its version")
        if pin_type in {"git-commit", "sha256"} and not component.source.startswith("https://"):
            raise CompatibilityError(f"component {index} source must use HTTPS")
        components.append(component)
    component_names = {item.name for item in components}
    if component_names != _EXPECTED_COMPONENTS or len(component_names) != len(components):
        raise CompatibilityError("editor compatibility component coverage is incomplete")

    raw_clients = _object(root.get("clients"), "clients")
    clients: list[EditorClient] = []
    for lane, value in sorted(raw_clients.items(), key=lambda item: str(item[0])):
        lane_name = str(lane)
        data = _object(value, f"clients.{lane_name}")
        expected_fields = {"name", "version", "component"}
        if lane_name == "zed":
            expected_fields.add("x11_app_id")
        _exact_keys(data, expected_fields, f"clients.{lane_name}")
        client = EditorClient(
            lane=lane_name,
            name=_text(data, "name", f"clients.{lane_name}"),
            version=_text(data, "version", f"clients.{lane_name}"),
            component=_text(data, "component", f"clients.{lane_name}"),
            x11_app_id=(
                _text(data, "x11_app_id", f"clients.{lane_name}") if lane_name == "zed" else ""
            ),
        )
        expected_client = _EXPECTED_CLIENTS.get(lane_name)
        if (
            expected_client is None
            or (
                client.name,
                client.version,
                client.component,
                client.x11_app_id,
            )
            != expected_client
        ):
            raise CompatibilityError(f"editor client {lane_name} differs from the wire contract")
        clients.append(client)
    if {item.lane for item in clients} != set(_EXPECTED_CLIENTS):
        raise CompatibilityError("editor client lane coverage is incomplete")

    return Compatibility(
        repository=repository,
        release_api=release_api,
        version=version,
        tag=tag,
        tag_commit=tag_commit,
        artifacts=tuple(artifacts),
        components=tuple(components),
        clients=tuple(clients),
    )


def assert_repository_contract(contract: Compatibility, root: Path = ROOT) -> None:
    """Require every duplicated executable/doc pin to match the manifest."""
    surfaces = {
        "stream": root / "src/synapse_channel/participants/opencode_stream.py",
        "fixture": root / "tests/fixtures/opencode/process.py",
        "docs": root / "docs/opencode.md",
        "integration": root / ".github/workflows/opencode-integration.yml",
        "editors": root / ".github/workflows/opencode-editor-e2e.yml",
        "compatibility": root / ".github/workflows/opencode-compatibility.yml",
        "zed_x11": root / "tests/e2e/opencode_editors/zed_x11.py",
    }
    text = {name: path.read_text(encoding="utf-8") for name, path in surfaces.items()}
    for name in ("stream", "fixture", "docs", "integration", "editors", "compatibility"):
        if contract.version not in text[name]:
            raise CompatibilityError(f"{name} surface omitted OpenCode {contract.version}")
    linux = contract.artifact("linux-x64")
    for token in (linux.name, linux.sha256):
        if token not in text["integration"] or token not in text["editors"]:
            raise CompatibilityError(f"OpenCode workflows drifted from {token}")
    for component in contract.components:
        if component.pin not in text["editors"]:
            raise CompatibilityError(f"editor workflow omitted {component.name} pin")
    try:
        assert_editor_workflow_contract(
            text["editors"],
            surfaces["editors"],
            expected_lanes={client.lane for client in contract.clients},
        )
    except EditorWorkflowError as exc:
        raise CompatibilityError(str(exc)) from exc
    for client in contract.clients:
        if client.name not in text["docs"] or client.version not in text["docs"]:
            raise CompatibilityError(f"OpenCode documentation omitted {client.lane} wire identity")
        if client.x11_app_id and (
            f'_PINNED_ZED_APP_ID = "{client.x11_app_id}"' not in text["zed_x11"]
            or f"`{client.x11_app_id}`" not in text["docs"]
        ):
            raise CompatibilityError("Zed X11 identity drifted from the pinned runtime contract")
    for artifact in contract.artifacts:
        if artifact.smoke and (
            artifact.key not in text["compatibility"]
            or artifact.runner not in text["compatibility"]
        ):
            raise CompatibilityError(f"compatibility workflow omitted {artifact.key} runner")


def _version(value: str) -> tuple[int, ...]:
    raw = value[1:] if value.startswith("v") else value
    if not raw or any(not part.isdigit() for part in raw.split(".")):
        raise CompatibilityError(f"release version is not numeric SemVer: {value!r}")
    return tuple(int(part) for part in raw.split("."))


def verify_upstream(
    contract: Compatibility,
    release: Mapping[str, Any],
    latest: Mapping[str, Any],
    tag_ref: Mapping[str, Any],
) -> dict[str, object]:
    """Verify immutable release evidence and report latest-version drift."""
    if release.get("tag_name") != contract.tag:
        raise CompatibilityError("official release tag differs from the pinned tag")
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise CompatibilityError("pinned OpenCode release is not a published stable release")
    tag_object = _object(tag_ref.get("object"), "tag_ref.object")
    if tag_object.get("type") != "commit" or tag_object.get("sha") != contract.tag_commit:
        raise CompatibilityError("official Git tag ref differs from the pinned commit")
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise CompatibilityError("official release assets are absent")
    official: dict[str, Mapping[str, Any]] = {}
    for value in assets:
        data = _object(value, "release asset")
        name = data.get("name")
        if isinstance(name, str):
            if name in official:
                raise CompatibilityError(f"official release duplicated asset name: {name}")
            official[name] = data
    for artifact in contract.artifacts:
        asset = official.get(artifact.name)
        if asset is None or asset.get("digest") != f"sha256:{artifact.sha256}":
            raise CompatibilityError(f"official digest differs for {artifact.name}")
        expected_url = (
            f"https://github.com/{contract.repository}/releases/download/"
            f"{contract.tag}/{artifact.name}"
        )
        if asset.get("browser_download_url") != expected_url:
            raise CompatibilityError(f"official URL differs for {artifact.name}")

    latest_tag = latest.get("tag_name")
    if not isinstance(latest_tag, str):
        raise CompatibilityError("official latest release has no tag")
    pinned_version, latest_version = _version(contract.version), _version(latest_tag)
    if latest_version < pinned_version:
        raise CompatibilityError("official latest release is older than the pinned release")
    return {
        "artifact_count": len(contract.artifacts),
        "latest_tag": latest_tag,
        "pinned_tag": contract.tag,
        "pinned_is_latest": latest_version == pinned_version,
        "update_available": latest_version > pinned_version,
    }


def _json_object(path: Path, where: str) -> Mapping[str, Any]:
    try:
        return _object(json.loads(path.read_text(encoding="utf-8")), where)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"cannot read {where}: {path}") from exc


def main() -> int:
    """Validate local pins and optional official GitHub release evidence."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release-json", type=Path)
    parser.add_argument("--latest-json", type=Path)
    parser.add_argument("--tag-ref-json", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    contract = load_compatibility(args.manifest)
    assert_repository_contract(contract)
    evidence_paths = (args.release_json, args.latest_json, args.tag_ref_json)
    if any(evidence_paths) and not all(evidence_paths):
        parser.error("release, latest, and tag-ref JSON must be supplied together")
    report: dict[str, object] = {
        "artifact_count": len(contract.artifacts),
        "client_count": len(contract.clients),
        "component_count": len(contract.components),
        "pinned_tag": contract.tag,
    }
    if all(evidence_paths):
        report.update(
            verify_upstream(
                contract,
                _json_object(args.release_json, "release JSON"),
                _json_object(args.latest_json, "latest JSON"),
                _json_object(args.tag_ref_json, "tag-ref JSON"),
            )
        )
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"pinned_tag={contract.tag}\n")
            output.write(f"latest_tag={report.get('latest_tag', contract.tag)}\n")
            output.write(f"update_available={str(report.get('update_available', False)).lower()}\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
