# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — dependency-neutral hub transport and capacity defaults
"""Hub constants shared by runtime construction and grouped configuration.

Keeping these scalar defaults below both :mod:`hub` and :mod:`hub_config`
removes their former import inversion while preserving the values re-exported
from :mod:`hub` for existing callers.
"""

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8876
DEFAULT_MAX_HISTORY = 10000
DEFAULT_MAX_QUEUE = 64
DEFAULT_MAX_FINDINGS_PER_AGENT = 512
"""Maximum durable findings one agent may admit before private rejection."""
DEFAULT_RELAY_MAX_LINES = 5000
DEFAULT_PING_INTERVAL = 15.0
"""Seconds between server keepalive pings, so a dead socket is detected promptly."""
DEFAULT_PING_TIMEOUT = 15.0
"""Seconds to wait for a ping reply before dropping the connection and freeing its name."""
DEFAULT_MAX_CLIENTS = 256
"""Maximum simultaneous connections; a further connect is closed with code 4013.

Sized for a real multi-project fleet rather than a single demo. Each terminal
holds two sockets — its command connection and its persistent ``-rx`` waiter —
and presence daemons add more, so a few dozen active terminals quickly exceed a
low ceiling. When the older default of 64 was hit, every new connection was
rejected with 4013 while already-connected agents kept working, which read as a
silent hub outage to anyone trying to join. Operators on constrained hosts can
still lower this with ``--max-clients``.
"""
DEFAULT_MAX_MSG_BYTES = 1024 * 1024
"""Largest accepted inbound frame (bytes); a larger one is rejected by the transport."""
DEFAULT_TAKEOVER_COOLDOWN = 2.0
"""Seconds a name is protected from a second takeover, to blunt an eviction storm."""
DEFAULT_TAKEOVER_OSCILLATION_WINDOW = 30.0
"""Seconds over which repeated takeovers of one name are counted as an oscillation."""
DEFAULT_TAKEOVER_OSCILLATION_THRESHOLD = 5
"""Takeovers of one name within the window that trip quarantine (two waiters at war)."""
DEFAULT_TAKEOVER_QUARANTINE = 60.0
"""Seconds a thrashing name is pinned to its current owner, refusing all takeovers."""
DEFAULT_AUTH_TIMEOUT = 10.0
"""Seconds to wait for a name-binding first frame before closing an idle socket.

Applies on both open and secured hubs: a secured hub additionally requires the
first frame to authenticate; an open hub only requires a registration that binds
a name. Idle sockets that never bind are reaped so they cannot hold capacity or
per-host slots indefinitely.
"""
DEFAULT_MAX_CONNECTIONS_PER_HOST = 32
"""Default simultaneous sockets admitted from one remote host.

A multi-terminal workstation (command socket + ``-rx`` waiter per seat, plus
presence) routinely opens many sockets from one host. ``32`` admits a modest
local fleet while still bounding a single-host connection flood. Pass ``None``
(or CLI ``--max-connections-per-host 0``) to disable the cap; ``--secure`` clamps
to the stricter secure-mode ceiling.
"""
DEFAULT_SHUTDOWN_CLOSE_TIMEOUT = 5.0
"""Seconds allowed for WebSocket close handshakes during hub shutdown."""
MAX_LOG_PAYLOAD = 120
"""Characters of a message payload logged at INFO before it is truncated."""
DEFAULT_COMPACT_HINT_THRESHOLD = 100_000
"""Event-log record count past which the hub logs a one-off ``synapse compact`` hint.

The durable log grows append-only and is never auto-compacted — pruning is safe only
below a sequence the read-side has already consumed, which the hub cannot know. So
instead of silently growing or unsafely trimming, a hub started on a log larger than
this emits a single startup hint to run :class:`compact` manually.
"""
