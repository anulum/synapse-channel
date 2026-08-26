# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the shipped container compose file starts a hub that can bind

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
LOCAL_DEVELOPMENT_COMPOSE = ROOT / "docker-compose.local-development.yml"
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker.yml"
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
CONTAINER_BUILD_REQUIREMENTS = (
    ROOT / ".github" / "requirements" / "requirements-container-build.txt"
)
CONTAINER_REQUIREMENTS = ROOT / ".github" / "requirements" / "requirements-container.txt"


def _load(path: Path) -> dict[str, Any]:
    """Parse a YAML deployment artifact into a mapping."""
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def _hub_command(path: Path = COMPOSE) -> list[str]:
    """Return the hub service's command tokens from one Compose file."""
    service = _load(path)["services"]["hub"]
    command = service.get("command", [])
    assert isinstance(command, list), "the hub command must be an explicit argv list"
    return [str(token) for token in command]


def test_production_compose_has_no_insecure_runtime_override() -> None:
    """The canonical production profile must remain fail-closed."""
    command = _hub_command()
    assert "--host=0.0.0.0" in command
    assert not any(token.startswith("--insecure-") for token in command)
    assert not any(token.startswith("--relay-log") for token in command)
    for required in (
        "--token-file=/run/synapse/token",
        "--db-key-file=/run/synapse/db.key",
        "--tls-certfile=/run/synapse/tls.crt",
        "--tls-keyfile=/run/synapse/tls.key",
    ):
        assert required in command


def test_production_compose_mounts_owner_custody_inputs_read_only() -> None:
    """Production refuses ambient env secrets and requires explicit files."""
    service = _load(COMPOSE)["services"]["hub"]
    assert str(service["user"]).startswith("${SYNAPSE_UID:?")
    mounts = {str(item["target"]): item for item in service["volumes"]}
    assert set(mounts) == {
        "/data",
        "/run/synapse/token",
        "/run/synapse/db.key",
        "/run/synapse/tls.crt",
        "/run/synapse/tls.key",
    }
    assert all(mounts[path]["read_only"] is True for path in mounts if path != "/data")
    assert service["ports"] == ["127.0.0.1:8876:8876"]


def test_container_health_uses_the_declared_concrete_host_authority() -> None:
    """Wildcard binds must not make the Host guard reject the liveness probe."""
    authority = "127.0.0.1:8876"
    assert f"--advertised-host={authority}" in _hub_command()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f'"--advertised-host", "{authority}"' in dockerfile
    assert f'"--uri", "ws://{authority}"' in dockerfile


def test_local_development_downgrade_is_loud_bounded_and_separate() -> None:
    """The insecure topology exists only in its explicit local-dev file."""
    document = _load(LOCAL_DEVELOPMENT_COMPOSE)
    services = document["services"]
    assert set(services) == {"hub"}
    assert services["hub"]["ports"] == ["127.0.0.1:8876:8876"]
    command = _hub_command(LOCAL_DEVELOPMENT_COMPOSE)
    assert "--insecure-off-loopback" in command
    assert "--insecure-plaintext-at-rest" in command
    prose = LOCAL_DEVELOPMENT_COMPOSE.read_text(encoding="utf-8")
    assert "INSECURE LOCAL DEVELOPMENT ONLY" in prose
    assert "NEVER USE IN PRODUCTION" in prose


def test_docker_workflow_smoke_tests_the_compose_file() -> None:
    """CI must exercise the compose file, not only build the image.

    The compose file's start-up (which caught the refuse-to-bind default) is only
    guarded if a workflow actually runs ``docker compose up`` against it.
    """
    workflow = _load(DOCKER_WORKFLOW)
    assert "compose-smoke" in workflow["jobs"], "docker workflow needs a compose-smoke job"
    steps = workflow["jobs"]["compose-smoke"]["steps"]
    run_scripts = " ".join(str(step.get("run", "")) for step in steps)
    assert "docker compose up" in run_scripts
    assert "Create owner-only Compose smoke credentials" in [
        str(step.get("name", "")) for step in steps
    ]
    assert "openssl rand 32" in run_scripts
    assert "--uri wss://127.0.0.1:8876" in run_scripts
    assert "--token-file /run/synapse/token" in run_scripts
    assert 'header != b"SQLite format 3\\x00"' in run_scripts


def test_docker_workflow_can_publish_an_immutable_release_tag_after_automation() -> None:
    """A trusted dispatch must publish the named tag, never mutable main content."""
    source = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    workflow = _load(DOCKER_WORKFLOW)
    image = workflow["jobs"]["release-image"]
    steps = image["steps"]

    assert "\n  workflow_dispatch:" in source
    assert "release_tag:" in source
    assert "ref: ${{ inputs.release_tag || github.ref }}" in source
    assert 'git rev-list -n 1 "$release_tag"' in source
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+" in source

    login = next(step for step in steps if step.get("name") == "Log in to GHCR")
    build = next(
        step for step in steps if step.get("name") == "Build and push the immutable release image"
    )
    assert "if" not in login
    assert build["with"]["push"] is True
    assert "type=raw,value=${{ steps.release.outputs.tag }}" in source
    assert "type=raw,value=latest" in source


def test_dockerfile_installs_only_hash_locked_build_and_runtime_inputs() -> None:
    """The image must never resolve build backends or wheel dependencies live."""
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    _, build_stage, runtime_stage = dockerfile.split("FROM python:3.13-slim@")

    assert "requirements-container-build.txt" in build_stage
    assert "--require-hashes" in build_stage
    assert "--only-binary=:all:" in build_stage
    assert "python -m build --wheel --no-isolation" in build_stage
    assert "pip install --no-cache-dir build" not in build_stage
    assert "COPY LICENSE NOTICE.md ./" in build_stage
    assert "COPY LICENSES ./LICENSES" in build_stage

    assert "requirements-container.txt" in runtime_stage
    assert "--require-hashes" in runtime_stage
    assert "--only-binary=:all:" in runtime_stage
    assert "--no-index /tmp/*.whl" in runtime_stage
    assert runtime_stage.index("-r /tmp/requirements-container.txt") < runtime_stage.index(
        "--no-index /tmp/*.whl"
    )


def test_container_runtime_lock_matches_the_base_project_dependency() -> None:
    """The base image closure stays exact, hashed, and aligned with metadata."""
    lock = CONTAINER_REQUIREMENTS.read_text(encoding="utf-8")
    assert "websockets==16.0" in lock
    assert "--hash=sha256:95724e638f0f9c350bb1c2b0a7ad0e83d9cc0c9259f3ea94e40d7b02a2179ae5" in lock
    assert "sqlcipher3-binary==0.6.0" in lock
    assert "--hash=sha256:8a6afbdef7cbbb33b1228ce96edc1bfe7f15bdf2a5e8bdab87261ab52e4111e6" in lock
    assert lock.count("--hash=sha256:") == 2
    assert "websockets>=13.0" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_container_build_lock_contains_only_the_required_exact_toolchain() -> None:
    """The throwaway builder installs no unrelated CI or release utilities."""
    lock = CONTAINER_BUILD_REQUIREMENTS.read_text(encoding="utf-8")
    packages = {
        line.split("==", maxsplit=1)[0]
        for line in lock.splitlines()
        if line and not line.startswith(("#", " "))
    }
    assert packages == {"build", "packaging", "pyproject-hooks", "setuptools", "wheel"}
    assert lock.count("--hash=sha256:") == 8


def test_docker_context_exposes_only_the_two_required_lock_files() -> None:
    """Docker can read its locks without admitting the complete GitHub tree."""
    ignored = DOCKERIGNORE.read_text(encoding="utf-8")
    assert ".github/*" in ignored
    assert "!.github/requirements/requirements-container-build.txt" in ignored
    assert "!.github/requirements/requirements-container.txt" in ignored


def test_release_image_is_attested_and_bound_to_release_assets() -> None:
    """The published digest, SPDX SBOM, and bundles must converge in one manifest."""
    source = DOCKER_WORKFLOW.read_text(encoding="utf-8")
    release_source = source.split("\n  release-image:", maxsplit=1)[1]
    workflow = _load(DOCKER_WORKFLOW)
    release_image = workflow["jobs"]["release-image"]
    permissions = release_image["permissions"]

    assert permissions == {
        "actions": "read",
        "artifact-metadata": "write",
        "attestations": "write",
        "contents": "write",
        "id-token": "write",
        "packages": "write",
    }
    assert "anchore/sbom-action@e22c389904149dbc22b58101806040fa8d37a610" in source
    assert "syft-version: v1.50.0" in source
    assert source.count("actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6") == 2
    assert source.count("push-to-registry: true") == 2
    assert "tools/container_release_manifest.py" in source
    assert "subject-digest: ${{ steps.build.outputs.digest }}" in source
    assert "name: container-release-${{ steps.release.outputs.tag }}" in source
    assert 'gh release upload "$RELEASE_TAG" "$asset"' in source
    assert "--clobber" not in source
    assert release_source.index("id: build") < release_source.index(
        "Generate the published image SBOM"
    )
    assert release_source.index("Generate the published image SBOM") < release_source.index(
        "tools/container_release_manifest.py"
    )
    assert release_source.index("tools/container_release_manifest.py") < release_source.index(
        'gh release upload "$RELEASE_TAG" "$asset"'
    )
