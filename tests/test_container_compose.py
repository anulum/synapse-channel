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
DOCKER_WORKFLOW = ROOT / ".github" / "workflows" / "docker.yml"
DOCKERFILE = ROOT / "Dockerfile"


def _load(path: Path) -> dict[str, Any]:
    """Parse a YAML deployment artifact into a mapping."""
    return cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")))


def _hub_command() -> list[str]:
    """Return the hub service's command tokens from the shipped compose file."""
    service = _load(COMPOSE)["services"]["hub"]
    command = service.get("command", [])
    assert isinstance(command, list), "the hub command must be an explicit argv list"
    return [str(token) for token in command]


def test_compose_hub_that_binds_off_loopback_can_actually_start() -> None:
    """A 0.0.0.0 bind must pair with a token or the explicit off-loopback opt-in.

    The hub refuses to bind a non-loopback host with no token — a security guard. A
    container must bind 0.0.0.0 for its published port to reach it, so the shipped
    compose command has to carry the matching opt-in, or the container crash-loops on
    "Refusing to bind" and `docker compose up` never yields a hub.
    """
    command = _hub_command()
    binds_off_loopback = "--host=0.0.0.0" in command or "0.0.0.0" in command
    if not binds_off_loopback:
        return  # a loopback bind needs no opt-in
    has_token = any(token.startswith("--token") for token in command)
    accepts_off_loopback = "--insecure-off-loopback" in command
    assert has_token or accepts_off_loopback, (
        "a 0.0.0.0 bind needs --token or --insecure-off-loopback or the hub refuses to start"
    )


def test_compose_off_loopback_bind_is_published_on_loopback_only() -> None:
    """`--insecure-off-loopback` is safe only when the host publish stays on loopback.

    The container binding 0.0.0.0 without a token is acceptable precisely because the
    port is published to ``127.0.0.1`` on the host, so nothing off this machine can
    reach it. If that opt-in is present, every published port must be loopback-bound.
    """
    service = _load(COMPOSE)["services"]["hub"]
    if "--insecure-off-loopback" not in _hub_command():
        return
    ports = service.get("ports", [])
    assert ports, "an off-loopback hub must publish through an explicit loopback port"
    for mapping in ports:
        assert str(mapping).startswith("127.0.0.1:"), (
            f"off-loopback bind must be published on loopback only, got {mapping!r}"
        )


def test_container_health_uses_the_declared_concrete_host_authority() -> None:
    """Wildcard binds must not make the Host guard reject the liveness probe."""
    authority = "127.0.0.1:8876"
    assert f"--advertised-host={authority}" in _hub_command()
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f'"--advertised-host", "{authority}"' in dockerfile
    assert f'"--uri", "ws://{authority}"' in dockerfile


def test_compose_off_loopback_hub_sits_on_a_dedicated_single_service_network() -> None:
    """The container-peer audience must be bounded, not only host ingress.

    Containers attached to the hub's network reach ``hub:8876`` container-to-container,
    regardless of the host publish flags, so an ``--insecure-off-loopback`` hub must sit
    on an explicitly declared network that no other compose service shares — and the
    file must explain that boundary where the next operator will edit it.
    """
    document = _load(COMPOSE)
    services = document["services"]
    if "--insecure-off-loopback" not in _hub_command():
        return
    hub_networks = list(services["hub"].get("networks", []))
    assert hub_networks, "an off-loopback hub must be attached to an explicit named network"
    declared = document.get("networks") or {}
    for network in hub_networks:
        assert network in declared, f"hub network {network!r} must be declared in the file"
    for name, service in services.items():
        if name == "hub":
            continue
        shared = set(service.get("networks", [])) & set(hub_networks)
        assert not shared, f"service {name!r} shares the hub network {sorted(shared)} untokened"
    comments = COMPOSE.read_text(encoding="utf-8")
    assert "regardless of" in comments, "the container-peer reachability comment must stay"
    assert "keep untrusted services off this network" in comments


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
    assert "synapse health --uri ws://127.0.0.1:8876" in run_scripts
