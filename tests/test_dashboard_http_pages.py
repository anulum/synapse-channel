# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — page shells and snapshot pages through real HTTP and hub
"""Exercise all page owners without substituting the live snapshot collector."""

from __future__ import annotations

import asyncio
import json
from urllib.request import Request, urlopen

from hub_e2e_helpers import close_agents, connect_agent, running_hub
from synapse_channel.dashboard import DashboardServer, start_dashboard_server


def page(server: DashboardServer, path: str) -> tuple[int, dict[str, str], str]:
    """Read an admitted page with its complete actual response headers."""
    assert server.dashboard_token is not None
    request = Request(
        server.url(path), headers={"Authorization": f"Bearer {server.dashboard_token}"}
    )
    with urlopen(request, timeout=3.0) as response:  # nosec B310 - owned loopback server
        return response.status, dict(response.headers), response.read().decode()


async def test_shells_and_snapshot_pages_keep_one_real_hub_identity() -> None:
    """All public page routes retain hardened headers and the live hub roster."""
    async with running_hub() as (_, uri):
        handle = await connect_agent("PAGES/worker", uri)
        server = start_dashboard_server(
            host="127.0.0.1",
            port=0,
            uri=uri,
            name="PAGES/observer",
            token=None,
            ready_timeout=2.0,
            response_timeout=2.0,
            refresh_seconds=2,
            allow_non_loopback=False,
        )
        try:
            for path in ("/", "/studio", "/studio/command"):
                status, headers, body = await asyncio.to_thread(page, server, path)
                assert status == 200 and "text/html" in headers["Content-Type"]
                assert headers["X-Content-Type-Options"] == "nosniff"
                assert "<html" in body
            for path in ("/snapshot.json", "/studio.json"):
                status, headers, body = await asyncio.to_thread(page, server, path)
                assert status == 200 and "application/json" in headers["Content-Type"]
                document = json.loads(body)
                assert "PAGES/worker" in json.dumps(document)
            status, _, body = await asyncio.to_thread(page, server, "/classic")
            assert status == 200 and "PAGES/worker" in body
        finally:
            server.close()
            await close_agents(handle)
