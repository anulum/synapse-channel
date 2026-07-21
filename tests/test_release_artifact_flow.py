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
MCP_REGISTRY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "mcp-registry.yml"
SECURITY_POLICY = REPO_ROOT / "SECURITY.md"


def test_publish_and_release_reuse_one_digest_verified_artifact() -> None:
    release = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    publish = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    mcp_registry = MCP_REGISTRY_WORKFLOW.read_text(encoding="utf-8")
    security = SECURITY_POLICY.read_text(encoding="utf-8")
    build, after_build = publish.split("\n  integrity:", maxsplit=1)
    integrity, after_integrity = after_build.split("\n  attest:", maxsplit=1)
    attest, publish_job = after_integrity.split("\n  publish:", maxsplit=1)

    assert (release + publish).count("python -m build") == 1
    assert "python -m build --outdir release-artifact" in build
    assert "sha256sum -- *.whl *.tar.gz *-sbom.cdx.json > SHA256SUMS" in build
    assert "release-artifact/synapse-channel-${GITHUB_REF_NAME}-sbom.cdx.json" in build
    assert "name: release-dist" in build
    assert "actions/upload-artifact@" in build
    assert "id-token: write" not in build
    assert 'tags: ["v*"]' in publish
    assert "workflow_dispatch:" not in publish

    # The integrity gate installs the exact artifacts in clean environments,
    # loads every wheel console script from installed site-packages, and proves
    # wheel/sdist parity before signing or publication. It never rebuilds, so
    # the one verified build stays authoritative.
    assert "needs: build" in integrity
    assert "name: release-dist" in integrity
    assert "actions/download-artifact@" in integrity
    assert "import synapse_channel" in integrity
    assert "synapse --help" in integrity
    assert "tools/check_installed_console_scripts.py" in integrity
    assert "--project-metadata pyproject.toml" in integrity
    assert integrity.index("pip install --require-hashes --no-deps") < integrity.index(
        "tools/check_installed_console_scripts.py"
    )
    assert "tools/check_wheel_sdist_parity.py" in integrity
    assert "python -m build" not in integrity
    assert "id-token: write" not in integrity

    assert "needs: build" in attest
    assert "artifact-metadata: write" in attest
    assert "attestations: write" in attest
    assert "id-token: write" in attest
    assert "name: release-dist" in attest
    assert "sha256sum --check --strict SHA256SUMS" in attest
    assert "find . -maxdepth 1 -type f ! -name SHA256SUMS" in attest
    assert "actions/attest@f7c74d28b9d84cb8768d0b8ca14a4bac6ef463e6" in attest
    assert "subject-checksums: release-artifact/SHA256SUMS" in attest
    assert "steps.provenance.outputs.bundle-path" in attest
    assert "synapse-channel-${GITHUB_REF_NAME}-provenance.sigstore.json" in attest
    assert "name: release-provenance" in attest
    assert attest.index("sha256sum --check") < attest.index("actions/attest@")

    assert "needs: [build, attest, integrity]" in publish_job
    assert "id-token: write" in publish_job
    assert "actions/download-artifact@" in publish_job
    assert "sha256sum --check --strict SHA256SUMS" in publish_job
    assert "find . -maxdepth 1 -type f ! -name SHA256SUMS" in publish_job
    assert "cp release-artifact/*.whl release-artifact/*.tar.gz publish-dist/" in publish_job
    assert "packages-dir: publish-dist/" in publish_job
    assert "skip-existing:" not in publish
    assert publish.count("id-token: write") == 2
    assert publish_job.index("actions/download-artifact@") < publish_job.index("sha256sum --check")
    assert publish_job.index("sha256sum --check") < publish_job.index("cp release-artifact/*.whl")
    assert publish_job.index("cp release-artifact/*.whl") < publish_job.index(
        "gh-action-pypi-publish@"
    )

    assert "workflow_run:" in release
    assert "workflows: [publish]" in release
    assert "github.event.workflow_run.conclusion == 'success'" in release
    # The tag is derived from the published commit, not the unreliable
    # workflow_run.head_branch (which resolves to the branch containing a tagged
    # commit — e.g. main — and silently skipped the release job).
    assert "startsWith(github.event.workflow_run.head_branch" not in release
    assert 'git tag --points-at "$HEAD_SHA"' in release
    assert 'echo "tag=$tag" >> "$GITHUB_OUTPUT"' in release
    assert "ref: ${{ github.event.workflow_run.head_sha }}" in release
    assert "run-id: ${{ github.event.workflow_run.id }}" in release
    assert "github-token: ${{ secrets.GITHUB_TOKEN }}" in release
    assert release.count("actions/download-artifact@") == 2
    assert "name: release-dist" in release
    assert "name: release-provenance" in release
    assert "sha256sum --check --strict SHA256SUMS" in release
    assert "find . -maxdepth 1 -type f ! -name SHA256SUMS" in release
    assert "gh attestation verify" in release
    assert '--bundle "$PROVENANCE_BUNDLE"' in release
    assert '--repo "$GITHUB_REPOSITORY"' in release
    assert '"$GITHUB_REPOSITORY/.github/workflows/publish.yml"' in release
    assert '--source-ref "$SOURCE_REF"' in release
    assert '--source-digest "$SOURCE_SHA"' in release
    assert "--deny-self-hosted-runners" in release
    assert "tag_name: ${{ steps.tag.outputs.tag }}" in release
    assert "target_commitish: ${{ github.event.workflow_run.head_sha }}" in release
    assert "release-artifact/*" in release
    assert "release-provenance/*" in release
    assert "PyPI is then live while the GitHub Release is absent" in release
    assert release.index("actions/download-artifact@") < release.index("sha256sum --check")
    assert release.index("sha256sum --check") < release.index("gh attestation verify")
    assert release.index("gh attestation verify") < release.index("action-gh-release@")
    # The verified digests are also published in the release-notes body itself
    # (a fenced "Artifact checksums (SHA-256)" section), appended AFTER the strict
    # SHA256SUMS verification and BEFORE the GitHub Release is created, so a
    # consumer can confirm own-provenance straight from the notes.
    assert "## Artifact checksums (SHA-256)" in release
    assert "cat release-artifact/SHA256SUMS" in release
    assert release.index("sha256sum --check") < release.index("cat release-artifact/SHA256SUMS")
    assert release.index("cat release-artifact/SHA256SUMS") < release.index("action-gh-release@")
    assert "python -m build" not in release
    assert "id-token: write" not in release
    assert "actions: write" in release
    assert "gh workflow run docker.yml --ref main" in release
    assert "gh workflow run mcp-registry.yml --ref main" in release
    assert '--field release_tag="$RELEASE_TAG"' in release
    assert release.index("action-gh-release@") < release.index("gh workflow run docker.yml")
    assert release.index("gh workflow run docker.yml") < release.index(
        "gh workflow run mcp-registry.yml"
    )

    # MCP Registry publication is a recovery-safe, tag-bound OIDC workflow.
    # It publishes metadata only after proving that the immutable PyPI package
    # and the checked-out server.json describe the same release.
    assert "workflow_dispatch:" in mcp_registry
    assert "release_tag:" in mcp_registry
    assert "permissions: {}" in mcp_registry
    assert mcp_registry.count("id-token: write") == 1
    assert "contents: read" in mcp_registry
    assert "ref: refs/tags/${{ inputs.release_tag }}" in mcp_registry
    assert 'git show-ref --verify --quiet "$tag_ref"' in mcp_registry
    assert 'git rev-parse "$tag_ref^{commit}"' in mcp_registry
    assert 'metadata_version" != "$version' in mcp_registry
    assert "--phase package" in mcp_registry
    assert "--phase registry" in mcp_registry
    assert "PYTHONPATH=. python tools/verify_mcp_registry_release.py" in mcp_registry
    assert "releases/download/v1.7.9/mcp-publisher_linux_amd64.tar.gz" in mcp_registry
    assert "ab128162b0616090b47cf245afe0a23f3ef08936fdce19074f5ba0a4469281ac" in mcp_registry
    assert "sha256sum --check --strict" in mcp_registry
    assert "./mcp-publisher validate server.json" in mcp_registry
    assert "./mcp-publisher login github-oidc" in mcp_registry
    assert "./mcp-publisher publish server.json" in mcp_registry
    assert "steps.existing.outputs.published != 'true'" in mcp_registry
    assert "secrets." not in mcp_registry
    assert mcp_registry.index("--phase package") < mcp_registry.index(
        "./mcp-publisher login github-oidc"
    )
    assert mcp_registry.index("./mcp-publisher validate server.json") < mcp_registry.index(
        "./mcp-publisher login github-oidc"
    )
    assert mcp_registry.index("./mcp-publisher login github-oidc") < mcp_registry.index(
        "./mcp-publisher publish server.json"
    )

    assert "gh release download vX.Y.Z" in security
    assert "sha256sum --check SHA256SUMS" in security
    assert 'bundle="synapse-channel-vX.Y.Z-provenance.sigstore.json"' in security
    assert 'gh attestation verify "$artifact"' in security
    assert "--repo anulum/synapse-channel" in security
    assert "--signer-workflow anulum/synapse-channel/.github/workflows/publish.yml" in security
    assert "--source-ref refs/tags/vX.Y.Z" in security
    assert "--deny-self-hosted-runners" in security
