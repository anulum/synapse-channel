# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded per-principal remote MCP application
"""Provision distinct native hub seats and dispatch authenticated HTTP sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from pathlib import Path

from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from pydantic import AnyHttpUrl, TypeAdapter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send

from synapse_channel.mcp.bridge import SynapseHubBridge
from synapse_channel.mcp.http_agent import HttpHubAgent
from synapse_channel.mcp.http_auth import HttpTokenVerifier
from synapse_channel.mcp.http_config import load_http_auth_config
from synapse_channel.mcp.http_policy import HttpOperationPolicy
from synapse_channel.mcp.http_server import HttpMcpServer
from synapse_channel.mcp.http_views import ProjectMcpViews
from synapse_channel.mcp.registration import build_mcp_server


class HttpPrincipalRouter:
    """Route valid issuer subjects to their own fixed project and SDK session pool.

    Parameters
    ----------
    verifier : HttpTokenVerifier
        Shared trusted issuer, reloading current grants on each HTTP request.
    project : str
        Fixed operator-selected project.
    applications : dict[str, Starlette]
        Bounded provisioned subject applications, never created from requests.
    security : TransportSecuritySettings
        Explicit Host and Origin allowlists.
    max_requests : int
        Global upper bound on active HTTP requests, including long-lived GETs.
    """

    def __init__(
        self,
        verifier: HttpTokenVerifier,
        project: str,
        applications: dict[str, Starlette],
        security: TransportSecuritySettings,
        max_requests: int,
    ) -> None:
        self.verifier = verifier
        self.project = project
        self.applications = applications
        self.security = TransportSecurityMiddleware(security)
        self.max_requests = max_requests
        self.active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Reject unsafe origins, plaintext and excess requests before SDK dispatch."""
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        request = Request(scope, receive=receive)
        if scope["scheme"] != "https":
            await JSONResponse({"error": "HTTPS required"}, status_code=400)(scope, receive, send)
            return
        rejected = await self.security.validate_request(request, is_post=request.method == "POST")
        if rejected is not None:
            await rejected(scope, receive, send)
            return
        if self.active >= self.max_requests:
            await JSONResponse({"error": "request capacity reached"}, status_code=503)(
                scope, receive, send
            )
            return
        self.active += 1
        try:
            if request.url.path.startswith("/.well-known/"):
                await next(iter(self.applications.values()))(scope, receive, send)
                return
            authorization = request.headers.get("authorization", "")
            bearer = authorization[7:] if authorization.lower().startswith("bearer ") else ""
            principal = await self.verifier.authenticate(bearer)
            if principal is None:
                await JSONResponse(
                    {"error": "invalid_token"},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": 'Bearer resource_metadata="'
                        + str(build_resource_metadata_url(AnyHttpUrl(self.verifier.resource)))
                        + '"'
                    },
                )(scope, receive, send)
                return
            subject = principal.access_token.subject
            if self.project not in principal.authority.projects or subject not in self.applications:
                await JSONResponse({"error": "access denied"}, status_code=403)(
                    scope, receive, send
                )
                return
            await self.applications[subject](scope, receive, send)
        finally:
            self.active -= 1


def build_http_mcp_app(
    *,
    auth_file: str | Path,
    project: str,
    hub_uri: str,
    allowed_hosts: list[str],
    allowed_origins: list[str],
    hub_token: str | None = None,
    max_requests: int = 32,
    max_sessions: int = 8,
    request_bytes: int = 65536,
    reply_bytes: int = 262144,
    operation_timeout: float = 15.0,
    ready_timeout: float = 5.0,
) -> Starlette:
    """Build a private HTTPS profile using pre-enrolled subject-specific hub keys.

    Parameters
    ----------
    auth_file : str or pathlib.Path
        Secure issuer policy and provisioned seat grants.
    project : str
        One fixed project, not selected by an HTTP client.
    hub_uri : str
        Local or secured native WebSocket hub endpoint.
    allowed_hosts, allowed_origins : list[str]
        Explicit transport authority allowlists. Missing Origin remains valid for
        native clients; a supplied Origin must match.
    hub_token : str or None
        Separate operator-provided native hub credential, never an HTTP bearer.
    max_requests, max_sessions : int
        Global concurrent requests and per-principal SDK session limits.
    request_bytes, reply_bytes : int
        Maximum HTTP request body and returned action content.
    operation_timeout, ready_timeout : float
        Finite bridge dispatch and native admission startup bounds.

    Returns
    -------
    Starlette
        App whose lifespan owns only its provisioned agents and session managers.

    Raises
    ------
    ValueError
        When the profile, limits or selected project have no valid provisioned seats.
    RuntimeError
        At startup when a provisioned native identity is not admitted by the hub.
    """
    if (
        not allowed_hosts
        or not 1 <= max_requests <= 128
        or not 1 <= max_sessions <= 32
        or not 1024 <= request_bytes <= 1048576
        or not 0 < ready_timeout <= 30
    ):
        raise ValueError("invalid remote MCP transport limits")
    config = load_http_auth_config(auth_file)
    verifier = HttpTokenVerifier(auth_file)
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )
    bridges: list[SynapseHubBridge] = []
    applications: dict[str, Starlette] = {}
    for subject, authority in config.subjects.items():
        grant = authority.projects.get(project)
        if grant is None or not authority.enabled:
            continue
        bridge = SynapseHubBridge(
            uri=hub_uri,
            name=grant.seat,
            token=hub_token,
            agent_factory=partial(
                HttpHubAgent,
                identity_key_path=grant.identity_key_file,
                identity_key_id=grant.identity_key_id,
                machine_identity=False,
            ),
        )
        views = ProjectMcpViews(bridge, project)
        server = HttpMcpServer(
            HttpOperationPolicy(verifier, subject, project, grant),
            views,
            operation_timeout=operation_timeout,
            reply_bytes=reply_bytes,
            token_verifier=verifier,
            json_response=True,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(verifier.issuer),
                resource_server_url=AnyHttpUrl(verifier.resource),
                validate_token_resource=True,
                required_scopes=["synapse:read"],
            ),
            transport_security=security,
            max_sessions=max_sessions,
            max_request_body_size=request_bytes,
            session_idle_timeout=300.0,
        )
        build_mcp_server(bridge, server=server, views=views)
        bridges.append(bridge)
        applications[subject] = server.streamable_http_app()
    if not applications:
        raise ValueError("remote MCP project has no provisioned subjects")
    router = HttpPrincipalRouter(verifier, project, applications, security, max_requests)

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Admit all native identities before serving, then drain owned tasks on exit."""
        tasks: list[asyncio.Task[None]] = []
        try:
            async with AsyncExitStack() as stack:
                for bridge in bridges:
                    tasks.append(asyncio.create_task(bridge.agent.connect()))
                    if not await bridge.agent.wait_until_ready(ready_timeout):
                        raise RuntimeError("remote MCP provisioned hub identity was not admitted")
                    try:
                        status = await asyncio.wait_for(bridge.status(), ready_timeout)
                        TypeAdapter(dict[str, object]).validate_json(status, strict=True)
                    except Exception:
                        raise RuntimeError(
                            "remote MCP provisioned hub identity was not admitted"
                        ) from None
                for application in applications.values():
                    await stack.enter_async_context(
                        application.router.lifespan_context(application)
                    )
                yield
        finally:
            for bridge in bridges:
                bridge.agent.running = False
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    return Starlette(routes=[Mount("/", app=router)], lifespan=lifespan)
