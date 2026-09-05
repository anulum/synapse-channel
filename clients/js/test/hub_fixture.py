# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — isolated real hub for JavaScript integration tests

"""Serve an ephemeral authenticated hub until the parent closes stdin."""

import asyncio
import contextlib
import json
import sys

from synapse_channel.core.auth import TokenAuthenticator
from synapse_channel.core.hub import SynapseHub


async def main() -> None:
    """Publish readiness and shut down when the test parent disconnects."""
    hub = SynapseHub(authenticator=TokenAuthenticator(["integration-only-token"]))
    server = asyncio.create_task(hub.serve(host="127.0.0.1", port=0))
    try:
        host, port = await hub.wait_until_serving(timeout=5.0)
        print(json.dumps({"uri": f"ws://{host}:{port}"}), flush=True)
        await asyncio.to_thread(sys.stdin.read)
    finally:
        server.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server


if __name__ == "__main__":
    asyncio.run(main())
