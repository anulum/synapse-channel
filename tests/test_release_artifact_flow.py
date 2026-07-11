# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — release artifact flow invariants
"""Pin one verified distribution build across GitHub Release and PyPI."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"
PUBLISH_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish.yml"


def test_publish_and_release_reuse_one_digest_verified_artifact() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    publish = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert (release + publish).count("python -m build") == 1
    assert "python -m build --outdir release-artifact/packages" in publish
    assert "sha256sum packages/* sbom/* > SHA256SUMS" in publish
    assert "name: release-dist" in publish
    assert "actions/upload-artifact@" in publish
    assert "release-artifact/sbom/synapse-channel-${GITHUB_REF_NAME}-sbom.cdx.json" in publish
    assert 'tags: ["v*"]' in publish
    assert "workflow_dispatch:" not in publish
    assert "actions/download-artifact@" in publish
    assert "sha256sum --check --strict SHA256SUMS" in publish
    assert "find packages sbom -maxdepth 1 -type f" in publish
    assert "packages-dir: release-artifact/packages/" in publish
    assert "skip-existing:" not in publish
    assert publish.count("id-token: write") == 1
    assert publish.index("  publish:") < publish.index("id-token: write")
    assert publish.index("actions/download-artifact@") < publish.index("sha256sum --check")
    assert publish.index("sha256sum --check") < publish.index("gh-action-pypi-publish@")

    assert "workflow_run:" in release
    assert "workflows: [publish]" in release
    assert "github.event.workflow_run.conclusion == 'success'" in release
    assert "startsWith(github.event.workflow_run.head_branch, 'v')" in release
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in release
    assert "run-id: ${{ github.event.workflow_run.id }}" in release
    assert "github-token: ${{ secrets.GITHUB_TOKEN }}" in release
    assert "sha256sum --check --strict SHA256SUMS" in release
    assert "find packages sbom -maxdepth 1 -type f" in release
    assert "tag_name: ${{ github.event.workflow_run.head_branch }}" in release
    assert "target_commitish: ${{ github.event.workflow_run.head_sha }}" in release
    assert "release-artifact/sbom/*" in release
    assert "PyPI is then live while the GitHub Release is absent" in release
    assert release.index("actions/download-artifact@") < release.index("sha256sum --check")
    assert release.index("sha256sum --check") < release.index("action-gh-release@")
    assert "python -m build" not in release
    assert "id-token: write" not in release
