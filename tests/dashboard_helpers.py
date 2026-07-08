# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dashboard test helpers

"""Shared helpers for the dashboard HTTP/CLI test suite."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError
from urllib.request import Request, urlopen

if TYPE_CHECKING:
    from synapse_channel.dashboard import DashboardServer


def _http_get(url: str, *, authorization: str | None = None) -> tuple[int, str, str]:
    headers = {"Connection": "close"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=3) as response:  # nosec B310
            return (
                response.status,
                response.headers.get_content_type(),
                response.read().decode("utf-8"),
            )
    except HTTPError as exc:
        return exc.code, exc.headers.get_content_type(), exc.read().decode("utf-8")


def _feeds_server(
    *,
    reliability_db: Path | None = None,
    federation_store: Path | None = None,
    cockpit_dist: Path | None = None,
    dashboard_token: str | None = None,
) -> DashboardServer:
    """Start a dashboard with store feeds against an unreachable hub."""
    from synapse_channel.dashboard import start_dashboard_server

    return start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="SYNAPSE-CHANNEL/dashboard",
        token=None,
        dashboard_token=dashboard_token,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=5,
        allow_non_loopback=False,
        reliability_db=reliability_db,
        federation_store=federation_store,
        cockpit_dist=cockpit_dist,
    )
