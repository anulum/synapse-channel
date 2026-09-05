# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — file-backed Fleet browser test server
"""Serve real Core Fleet endpoints using only caller-owned test files."""

from __future__ import annotations

import json
import signal
import sys
import threading
from pathlib import Path

from synapse_channel.dashboard import start_dashboard_server


def main() -> int:
    """Serve the fixture directory until termination or a 60-second deadline.

    Returns
    -------
    int
        Zero after the dashboard has closed.
    """
    root = Path(sys.argv[1])
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    dashboard = start_dashboard_server(
        host="127.0.0.1",
        port=0,
        uri="ws://127.0.0.1:1",
        name="fleet-file-browser",
        token=None,
        ready_timeout=0.01,
        response_timeout=0.01,
        refresh_seconds=2,
        allow_non_loopback=False,
        dashboard_access_file=root / "access.json",
        fleet_observed_file=root / "mirror.json",
        fleet_observed_access_file=root / "grants.json",
    )
    try:
        print(json.dumps({"url": dashboard.url("/studio/command")}), flush=True)
        stop.wait(60)
    finally:
        dashboard.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
