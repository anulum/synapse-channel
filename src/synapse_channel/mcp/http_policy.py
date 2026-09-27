# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — remote MCP per-operation authority
"""Check fresh operator grants at the MCP tool and resource boundary."""

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import get_access_token

from synapse_channel.mcp.http_auth import HttpTokenVerifier
from synapse_channel.mcp.http_config import MUTATION_TOOLS, RemoteSeat


class HttpOperationPolicy:
    """Bind one server to an authenticated subject, project and provisioned hub seat.

    Parameters
    ----------
    verifier : HttpTokenVerifier
        Issuer verifier which reloads revocations and operator grants per operation.
    subject : str
        Verified issuer subject bound when this server was provisioned.
    project : str
        Operator-granted project served by this instance.
    seat : RemoteSeat
        Original provisioned seat and maximum tool authority for the server lifetime.

    Notes
    -----
    Removing permissions takes effect immediately. Adding permissions or changing
    the signing identity requires reprovisioning; an old session cannot gain them.
    """

    def __init__(
        self, verifier: HttpTokenVerifier, subject: str, project: str, seat: RemoteSeat
    ) -> None:
        self.verifier = verifier
        self.subject = subject
        self.project = project
        self.seat = seat

    async def authorize(self, tool: str) -> None:
        """Require both current token scope and an explicit operator tool grant.

        Parameters
        ----------
        tool : str
            Registered action name, also used for its corresponding read resource.

        Raises
        ------
        PermissionError
            If authentication, project, seat, tool or mutation authority is absent.
        """
        token = get_access_token()
        principal = await self.verifier.authenticate(token.token) if token is not None else None
        if principal is None or principal.access_token.subject != self.subject:
            raise PermissionError("remote MCP operation denied")
        current = principal.authority.projects.get(self.project)
        if (
            current is None
            or current.seat != self.seat.seat
            or current.identity_key_file != self.seat.identity_key_file
            or current.identity_key_id != self.seat.identity_key_id
            or tool not in current.tools
            or tool not in self.seat.tools
            or (tool in MUTATION_TOOLS and "synapse:mutate" not in principal.access_token.scopes)
        ):
            raise PermissionError("remote MCP operation denied")
