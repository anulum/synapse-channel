# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic container release manifest tests

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "container_release_manifest.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("container_release_manifest", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _evidence(tmp_path: Path) -> tuple[Path, Path, Path]:
    sbom = tmp_path / "image.spdx.json"
    provenance = tmp_path / "provenance.sigstore.json"
    sbom_attestation = tmp_path / "sbom.sigstore.json"
    _write_json(sbom, {"spdxVersion": "SPDX-2.3", "SPDXID": "SPDXRef-DOCUMENT"})
    _write_json(provenance, {"mediaType": "application/vnd.dev.sigstore.bundle+json;version=0.3"})
    _write_json(sbom_attestation, {"mediaType": "application/vnd.dev.sigstore.bundle+json"})
    return sbom, provenance, sbom_attestation


def _arguments(tmp_path: Path) -> list[str]:
    sbom, provenance, sbom_attestation = _evidence(tmp_path)
    return [
        "--image",
        "ghcr.io/anulum/synapse-channel",
        "--tag",
        "v0.99.15",
        "--digest",
        "sha256:" + "a" * 64,
        "--source-repository",
        "anulum/synapse-channel",
        "--source-commit",
        "b" * 40,
        "--sbom",
        str(sbom),
        "--provenance-bundle",
        str(provenance),
        "--sbom-attestation-bundle",
        str(sbom_attestation),
        "--output",
        str(tmp_path / "nested" / "manifest.json"),
    ]


def test_manifest_cli_binds_the_image_to_exact_release_evidence(tmp_path: Path) -> None:
    tool = _load_tool()
    arguments = _arguments(tmp_path)

    assert tool.main(arguments) == 0
    output = tmp_path / "nested" / "manifest.json"
    first = output.read_bytes()
    assert tool.main(arguments) == 0
    assert output.read_bytes() == first

    manifest = cast("dict[str, object]", json.loads(first))
    assert manifest["schema_version"] == "synapse-container-release.v1"
    assert manifest["source"] == {
        "repository": "anulum/synapse-channel",
        "ref": "refs/tags/v0.99.15",
        "commit": "b" * 40,
    }
    image = cast("dict[str, object]", manifest["image"])
    assert image["reference"] == "ghcr.io/anulum/synapse-channel@sha256:" + "a" * 64
    assert image["tag_reference"] == "ghcr.io/anulum/synapse-channel:v0.99.15"
    sbom = cast("dict[str, object]", manifest["sbom"])
    assert sbom["format"] == "spdx-json"
    assert sbom["name"] == "image.spdx.json"
    assert str(sbom["digest"]).startswith("sha256:")
    assert sbom["size"] == (tmp_path / "image.spdx.json").stat().st_size
    attestations = cast("dict[str, dict[str, object]]", manifest["attestations"])
    assert attestations["build_provenance"]["name"] == "provenance.sigstore.json"
    assert attestations["sbom"]["name"] == "sbom.sigstore.json"


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--image", "GHCR.IO/anulum/synapse-channel", "lowercase OCI name"),
        ("--tag", "latest", "vX.Y.Z release tag"),
        ("--digest", "sha256:1234", "64 lowercase hex"),
        ("--source-repository", "anulum", "owner/name"),
        ("--source-commit", "ABC", "40 lowercase hex"),
    ],
)
def test_manifest_cli_rejects_mutable_or_malformed_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str, message: str
) -> None:
    tool = _load_tool()
    arguments = _arguments(tmp_path)
    arguments[arguments.index(flag) + 1] = value

    with pytest.raises(SystemExit, match="2"):
        tool.main(arguments)

    assert message in capsys.readouterr().err


@pytest.mark.parametrize(
    ("filename", "document", "message"),
    [
        ("image.spdx.json", {"spdxVersion": "SPDX-2.2", "SPDXID": "SPDXRef-DOCUMENT"}, "SPDX-2.3"),
        ("image.spdx.json", {"spdxVersion": "SPDX-2.3", "SPDXID": "other"}, "document root"),
        ("provenance.sigstore.json", [], "JSON object"),
        ("sbom.sigstore.json", "bundle", "JSON object"),
    ],
)
def test_manifest_cli_rejects_evidence_with_the_wrong_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    document: object,
    message: str,
) -> None:
    tool = _load_tool()
    arguments = _arguments(tmp_path)
    _write_json(tmp_path / filename, document)

    with pytest.raises(SystemExit, match="2"):
        tool.main(arguments)

    assert message in capsys.readouterr().err


@pytest.mark.parametrize("contents", ["not-json", ""])
def test_manifest_cli_rejects_unreadable_json_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str
) -> None:
    tool = _load_tool()
    arguments = _arguments(tmp_path)
    (tmp_path / "provenance.sigstore.json").write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit, match="2"):
        tool.main(arguments)

    assert "provenance bundle must be a readable JSON file" in capsys.readouterr().err


def test_manifest_cli_rejects_a_missing_evidence_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _load_tool()
    arguments = _arguments(tmp_path)
    (tmp_path / "image.spdx.json").unlink()

    with pytest.raises(SystemExit, match="2"):
        tool.main(arguments)

    assert "SBOM must be a readable JSON file" in capsys.readouterr().err
