# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — the dashboard server's start refusals and its close without feed caches
"""Two edges of the dashboard server that no other test reached."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from synapse_channel.dashboard import DashboardServer, start_dashboard_server


@pytest.mark.parametrize("pids", [(0,), (-3,), tuple(range(1, 258))])
def test_the_host_monitor_refuses_pids_before_the_server_starts(pids: tuple[int, ...]) -> None:
    with pytest.raises(ValueError, match="^host monitor accepts at most 256 positive PIDs$"):
        start_dashboard_server(
            host="127.0.0.1",
            port=0,
            uri="ws://127.0.0.1:1",
            name="SYNAPSE-CHANNEL/dashboard",
            token=None,
            ready_timeout=0.01,
            response_timeout=0.01,
            refresh_seconds=5,
            allow_non_loopback=False,
            host_session_pids=pids,
        )


def test_a_server_without_feed_caches_closes_and_joins_its_thread() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    DashboardServer(server=server, thread=thread).close()

    assert not thread.is_alive()
