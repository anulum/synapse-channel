# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the MCP client bearer never reaches the SYNAPSE hub
"""An HTTP MCP client's bearer authenticates it to this server and nothing else.

The production HTTPS application reaches a real, token-secured hub with the
operator's hub credential and the provisioned seat's signing key. This test
drives every mutation tool and a resource read with an issuer-signed bearer
and records every frame the hub receives. Neither the bearer nor any of its
three JWT segments appears, while the hub credential, the seat and the message
text do, which shows that the recording would have seen a leak.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl

from hub_e2e_helpers import running_hub
from hub_wire_recorder import WireRecordingHub
from mcp_http_helpers import access_token, https_server, provision
from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.persistence import EventStore
from synapse_channel.mcp.http_application import build_http_mcp_app

HUB_TOKEN = "mcp-hub-credential-5e0b19c4"
SOURCE = Path(__file__).resolve().parents[1] / "src"
OUTBOUND = frozenset(
    {"synapse_channel.core.mcp_outbound", "synapse_channel.core.mcp_config_launch"}
)


async def test_bearer_never_reaches_the_hub_through_any_mutation_or_read(tmp_path: Path) -> None:
    """Declare, claim, update, send, release and a resource read keep the bearer local."""
    issuer = Ed25519PrivateKey.generate()
    grants = tmp_path / "grants.json"
    trust = provision(grants, issuer)
    store = EventStore(tmp_path / "hub.db")
    hub = WireRecordingHub(
        journal=store,
        identity_trust_bundle=trust,
        require_identity_binding=True,
        authenticator=TokenAuthenticator([HUB_TOKEN]),
    )
    bearer = access_token(
        issuer, sub="alice", jti="alice-isolation", scope="synapse:read synapse:mutate"
    )
    calls: list[tuple[str, dict[str, Any]]] = [
        ("synapse_task_declare", {"task_id": "ALPHA/isolated", "title": "isolation canary"}),
        ("synapse_claim", {"task_id": "ALPHA/isolated", "task_only": True}),
        ("synapse_task_update", {"task_id": "ALPHA/isolated", "suggested_owner": "ALPHA/bob"}),
        ("synapse_send", {"target": "ALPHA/bob", "message": "isolation message canary"}),
        ("synapse_release", {"task_id": "ALPHA/isolated"}),
    ]
    try:
        async with running_hub(hub) as (_, uri):
            app = build_http_mcp_app(
                auth_file=grants,
                project="ALPHA",
                hub_uri=uri,
                allowed_hosts=["127.0.0.1:*"],
                allowed_origins=[],
                hub_token=HUB_TOKEN,
            )
            async with (
                https_server(app, tmp_path) as (url, tls),
                httpx.AsyncClient(
                    verify=tls,
                    trust_env=False,
                    timeout=5,
                    headers={"Authorization": "Bearer " + bearer},
                ) as client,
                streamable_http_client(url + "/mcp", http_client=client) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                for index, (name, arguments) in enumerate(calls):
                    result = await session.call_tool(
                        name, arguments, meta={"synapse/operation-id": f"isolation-{index}"}
                    )
                    assert not result.isError, name
                board = await session.read_resource(AnyUrl("synapse://board"))
                assert board.contents
    finally:
        store.close()
    received = hub.inbound_text()

    # The recording sees credentials, identities and content when they are on the wire ...
    assert any(HUB_TOKEN in frame for frame in received)
    assert any("ALPHA/alice" in frame for frame in received)
    assert any("isolation message canary" in frame for frame in received)
    # ... and neither the bearer nor any JWT segment of it is in any frame.
    for fragment in (bearer, *bearer.split(".")):
        assert all(fragment not in frame for frame in received)
    assert all("alice-isolation" not in frame for frame in received)


def _module_file(module: str) -> Path | None:
    """Return the source file of a ``synapse_channel`` module, if it is one."""
    base = SOURCE.joinpath(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _static_imports(module: str, path: Path) -> set[str]:
    """Return every ``synapse_channel`` module named by an import statement in ``path``."""
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")
            base = parts[: len(parts) - node.level + 1] if node.level else []
            stem = ".".join([*base, node.module] if node.module else base)
            found.add(stem)
            found.update(f"{stem}.{alias.name}" for alias in node.names)
    return {name for name in found if name.startswith("synapse_channel")}


def import_closure(roots: tuple[str, ...]) -> set[str]:
    """Return every in-package module reachable from ``roots`` by static imports."""
    seen: set[str] = set()
    pending = list(roots)
    while pending:
        module = pending.pop()
        path = _module_file(module)
        if module in seen or path is None:
            continue
        seen.add(module)
        pending.extend(_static_imports(module, path))
    return seen


def test_the_mcp_server_cannot_reach_the_outbound_mcp_client() -> None:
    """No server module imports the outbound client or its child-process launcher."""
    closure = import_closure(
        (
            "synapse_channel.mcp.http_application",
            "synapse_channel.mcp.server",
            "synapse_channel.mcp.stdio",
        )
    )

    assert "synapse_channel.mcp.http_auth" in closure
    assert "synapse_channel.mcp.bridge" in closure
    assert closure.isdisjoint(OUTBOUND)
    # The check itself sees the outbound client from the command that does use it.
    assert OUTBOUND & import_closure(("synapse_channel.cli_mcp_call",))
