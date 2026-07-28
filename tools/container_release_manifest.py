# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — deterministic container release evidence manifest
"""Bind one immutable image digest to its SBOM and attestation bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import cast

_IMAGE_RE = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?/[a-z0-9._/-]+$")
_TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[.-][0-9A-Za-z.-]+)?$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        document = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a readable JSON file") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return cast("dict[str, object]", document)


def _digest(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _artifact(path: Path) -> dict[str, object]:
    return {"name": path.name, "digest": _digest(path), "size": path.stat().st_size}


def build_manifest(
    *,
    image: str,
    tag: str,
    digest: str,
    source_repository: str,
    source_commit: str,
    sbom: Path,
    provenance_bundle: Path,
    sbom_attestation_bundle: Path,
) -> dict[str, object]:
    """Build a deterministic container release manifest.

    Parameters
    ----------
    image:
        Fully qualified OCI image name without a tag or digest.
    tag:
        Immutable semantic release tag beginning with ``v``.
    digest:
        Published image digest in ``sha256:<hex>`` form.
    source_repository:
        GitHub ``owner/repository`` source identity.
    source_commit:
        Forty-character source commit identifier.
    sbom:
        SPDX 2.3 JSON SBOM generated from the published image digest.
    provenance_bundle:
        Portable Sigstore bundle for the image build-provenance attestation.
    sbom_attestation_bundle:
        Portable Sigstore bundle binding the SBOM to the image digest.

    Returns
    -------
    dict[str, object]
        Versioned manifest containing immutable references and file digests.

    Raises
    ------
    ValueError
        If an identity, digest, or evidence document is malformed.
    """
    validators = (
        (_IMAGE_RE, image, "image must be a fully qualified lowercase OCI name"),
        (_TAG_RE, tag, "tag must be an immutable vX.Y.Z release tag"),
        (_DIGEST_RE, digest, "digest must use sha256 with 64 lowercase hex characters"),
        (_REPOSITORY_RE, source_repository, "source repository must be owner/name"),
        (_COMMIT_RE, source_commit, "source commit must contain 40 lowercase hex characters"),
    )
    for pattern, value, message in validators:
        if pattern.fullmatch(value) is None:
            raise ValueError(message)

    sbom_document = _load_json_object(sbom, "SBOM")
    if sbom_document.get("spdxVersion") != "SPDX-2.3":
        raise ValueError("SBOM must use SPDX-2.3 JSON")
    if sbom_document.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise ValueError("SBOM must identify the SPDX document root")
    _load_json_object(provenance_bundle, "provenance bundle")
    _load_json_object(sbom_attestation_bundle, "SBOM attestation bundle")

    return {
        "schema_version": "synapse-container-release.v1",
        "source": {
            "repository": source_repository,
            "ref": f"refs/tags/{tag}",
            "commit": source_commit,
        },
        "image": {
            "name": image,
            "tag": tag,
            "tag_reference": f"{image}:{tag}",
            "digest": digest,
            "reference": f"{image}@{digest}",
        },
        "sbom": {"format": "spdx-json", **_artifact(sbom)},
        "attestations": {
            "build_provenance": _artifact(provenance_bundle),
            "sbom": _artifact(sbom_attestation_bundle),
        },
    }


def write_manifest(manifest: dict[str, object], output: Path) -> None:
    """Atomically write a release manifest with stable JSON ordering."""
    output.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    with tempfile.TemporaryDirectory(prefix=f".{output.name}.", dir=output.parent) as raw_dir:
        temporary = Path(raw_dir) / output.name
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.chmod(0o644)
        temporary.replace(output)


def build_parser() -> argparse.ArgumentParser:
    """Create the container manifest command-line parser."""
    parser = argparse.ArgumentParser(
        description="Bind an immutable container image to its release evidence."
    )
    parser.add_argument("--image", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--provenance-bundle", type=Path, required=True)
    parser.add_argument("--sbom-attestation-bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Write a validated container release manifest and return an exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            image=args.image,
            tag=args.tag,
            digest=args.digest,
            source_repository=args.source_repository,
            source_commit=args.source_commit,
            sbom=args.sbom,
            provenance_bundle=args.provenance_bundle,
            sbom_attestation_bundle=args.sbom_attestation_bundle,
        )
    except ValueError as exc:
        parser.error(str(exc))
    write_manifest(manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
