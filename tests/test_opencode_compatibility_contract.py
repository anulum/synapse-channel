# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — OpenCode compatibility manifest and upstream evidence tests
"""Exercise the pinned compatibility contract through its public API and CLI."""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Compatibility,
        CompatibilityError,
        assert_repository_contract,
        load_compatibility,
        verify_upstream,
    )
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.opencode_compatibility_contract import (
        DEFAULT_MANIFEST,
        Compatibility,
        CompatibilityError,
        assert_repository_contract,
        load_compatibility,
        verify_upstream,
    )


def _manifest_data() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(DEFAULT_MANIFEST.read_text(encoding="utf-8")))


def _write_manifest(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _official_evidence(
    contract: Compatibility,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    release: dict[str, Any] = {
        "assets": [
            {
                "browser_download_url": (
                    f"https://github.com/{contract.repository}/releases/download/"
                    f"{contract.tag}/{artifact.name}"
                ),
                "digest": f"sha256:{artifact.sha256}",
                "name": artifact.name,
            }
            for artifact in contract.artifacts
        ],
        "draft": False,
        "prerelease": False,
        "tag_name": contract.tag,
    }
    latest = {"tag_name": contract.tag}
    tag_ref = {"object": {"sha": contract.tag_commit, "type": "commit"}}
    return release, latest, tag_ref


def test_repository_uses_one_complete_immutable_compatibility_contract() -> None:
    contract = load_compatibility()

    assert contract.repository == "anomalyco/opencode"
    assert contract.version == "1.17.20"
    assert len(contract.artifacts) == 12
    assert sum(artifact.smoke for artifact in contract.artifacts) == 5
    assert len(contract.components) == 11
    assert len(contract.clients) == 4
    assert contract.client("jetbrains").name == "JetBrains.IntelliJ IDEA"
    assert contract.client("neovim").version == "1.0.0"
    with pytest.raises(CompatibilityError, match="unknown OpenCode editor lane"):
        contract.client("unknown")
    assert_repository_contract(contract)


def test_repository_contract_refuses_documented_wire_identity_drift(tmp_path: Path) -> None:
    contract = load_compatibility()
    root = tmp_path / "repository"
    source_root = Path(__file__).resolve().parents[1]
    surfaces = (
        "src/synapse_channel/participants/opencode_stream.py",
        "tests/fixtures/opencode/process.py",
        "docs/opencode.md",
        ".github/workflows/opencode-integration.yml",
        ".github/workflows/opencode-editor-e2e.yml",
        ".github/workflows/opencode-compatibility.yml",
    )
    for relative in surfaces:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, destination)
    docs = root / "docs/opencode.md"
    docs.write_text(
        docs.read_text(encoding="utf-8").replace("CodeCompanion.nvim", "CodeCompanion"),
        encoding="utf-8",
    )

    with pytest.raises(CompatibilityError, match="neovim wire identity"):
        assert_repository_contract(contract, root)


def test_upstream_verifier_accepts_all_pinned_assets_and_reports_latest() -> None:
    contract = load_compatibility()
    release, latest, tag_ref = _official_evidence(contract)

    assert verify_upstream(contract, release, latest, tag_ref) == {
        "artifact_count": 12,
        "latest_tag": "v1.17.20",
        "pinned_tag": "v1.17.20",
        "pinned_is_latest": True,
        "update_available": False,
    }

    latest["tag_name"] = "v1.18.0"
    report = verify_upstream(contract, release, latest, tag_ref)
    assert report["pinned_is_latest"] is False
    assert report["update_available"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-platform", "incomplete platform coverage"),
        ("wrong-runner", "supported runner matrix"),
        ("missing-component", "component coverage is incomplete"),
        ("missing-client", "client lane coverage is incomplete"),
        ("wire-client", "differs from the wire contract"),
        ("runtime-pin", "runtime pin differs"),
        ("extra-field", "fields differ"),
        ("schema", "unsupported OpenCode compatibility schema"),
        ("repository", "official source"),
        ("release-api", "release API must match"),
        ("artifact-digest", "invalid integrity metadata"),
        ("artifact-layout", "supported release layout"),
        ("artifact-smoke", "pair smoke with an exact runner"),
        ("component-pin-type", "unknown pin type"),
        ("component-pin", "invalid commit pin"),
        ("component-source", "source must use HTTPS"),
    ],
)
def test_manifest_refuses_incomplete_or_widened_contracts(
    tmp_path: Path, mutation: str, message: str
) -> None:
    data = _manifest_data()
    artifacts = cast(dict[str, Any], data["artifacts"])
    clients = cast(dict[str, dict[str, str]], data["clients"])
    components = cast(list[dict[str, Any]], data["components"])
    if mutation == "missing-platform":
        del artifacts["windows-arm64"]
    elif mutation == "wrong-runner":
        cast(dict[str, Any], artifacts["linux-x64"])["runner"] = "ubuntu-latest"
    elif mutation == "missing-component":
        components.pop()
    elif mutation == "missing-client":
        del clients["zed"]
    elif mutation == "wire-client":
        clients["neovim"]["version"] = "19.19.0"
    elif mutation == "runtime-pin":
        next(row for row in components if row["name"] == "Emacs")["pin"] = "29.2"
    elif mutation == "extra-field":
        data["unreviewed"] = True
    elif mutation == "schema":
        data["schema_version"] = "unknown"
    elif mutation == "repository":
        cast(dict[str, Any], data["upstream"])["repository"] = "fork/opencode"
    elif mutation == "release-api":
        cast(dict[str, Any], data["upstream"])["release_api"] = "https://example.invalid"
    elif mutation == "artifact-digest":
        cast(dict[str, Any], artifacts["linux-x64"])["sha256"] = "not-a-digest"
    elif mutation == "artifact-layout":
        cast(dict[str, Any], artifacts["linux-x64"])["binary"] = "bin/opencode"
    elif mutation == "artifact-smoke":
        cast(dict[str, Any], artifacts["linux-x64"])["smoke"] = False
    elif mutation == "component-pin-type":
        components[0]["pin_type"] = "tag"
    elif mutation == "component-pin":
        next(row for row in components if row["name"] == "acp.el")["pin"] = "short"
    else:
        next(row for row in components if row["name"] == "acp.el")["source"] = (
            "git://example.invalid/acp.el"
        )

    with pytest.raises(CompatibilityError, match=message):
        load_compatibility(_write_manifest(tmp_path / "compatibility.json", data))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("digest", "official digest differs"),
        ("url", "official URL differs"),
        ("tag-ref", "official Git tag ref differs"),
        ("draft", "not a published stable release"),
        ("duplicate", "duplicated asset name"),
        ("older", "older than the pinned release"),
    ],
)
def test_upstream_verifier_fails_closed_on_release_drift(mutation: str, message: str) -> None:
    contract = load_compatibility()
    release, latest, tag_ref = _official_evidence(contract)
    if mutation == "digest":
        cast(dict[str, Any], release["assets"][0])["digest"] = "sha256:" + "0" * 64
    elif mutation == "url":
        cast(dict[str, Any], release["assets"][0])["browser_download_url"] = (
            "https://example.invalid/opencode"
        )
    elif mutation == "tag-ref":
        cast(dict[str, Any], tag_ref["object"])["sha"] = "0" * 40
    elif mutation == "draft":
        release["draft"] = True
    elif mutation == "duplicate":
        cast(list[dict[str, Any]], release["assets"]).append(
            copy.deepcopy(cast(list[dict[str, Any]], release["assets"])[0])
        )
    else:
        latest["tag_name"] = "v1.16.0"

    with pytest.raises(CompatibilityError, match=message):
        verify_upstream(contract, release, latest, tag_ref)


def test_contract_cli_writes_machine_readable_advisory(tmp_path: Path) -> None:
    contract = load_compatibility()
    release, latest, tag_ref = _official_evidence(contract)
    latest["tag_name"] = "v1.18.0"
    release_path = tmp_path / "release.json"
    latest_path = tmp_path / "latest.json"
    tag_ref_path = tmp_path / "tag-ref.json"
    output_path = tmp_path / "github-output"
    for path, value in (
        (release_path, release),
        (latest_path, latest),
        (tag_ref_path, tag_ref),
    ):
        path.write_text(json.dumps(value), encoding="utf-8")

    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "tools.opencode_compatibility_contract",
            "--release-json",
            str(release_path),
            "--latest-json",
            str(latest_path),
            "--tag-ref-json",
            str(tag_ref_path),
            "--github-output",
            str(output_path),
        ],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "artifact_count": 12,
        "client_count": 4,
        "component_count": 11,
        "latest_tag": "v1.18.0",
        "pinned_is_latest": False,
        "pinned_tag": "v1.17.20",
        "update_available": True,
    }
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        "pinned_tag=v1.17.20",
        "latest_tag=v1.18.0",
        "update_available=true",
    ]
