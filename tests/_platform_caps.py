# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — platform-capability guards for cross-OS test runs
"""Skip guards for tests that need a platform capability SYNAPSE only has on Linux.

The MCP sealed-executable launch copies executable bytes into a Linux ``memfd``
and binds them through a ``/proc/self/fd`` procfd path so a later pathname change
cannot authorise different code. Both primitives are Linux-only: macOS has no
``os.memfd_create`` and no ``/proc``, Windows has neither. On those platforms the
launch guard correctly reports "secure executable validation is unavailable on
this platform", so the tests that exercise the *working* mechanism cannot pass
there. They skip on any platform without the capability and run in full on Linux
CI, where the security surface actually lives. This mirrors the source guard in
:func:`synapse_channel.core.mcp_config_launch.bind_mcp_server_launch`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

SEALED_LAUNCH_AVAILABLE = (
    os.name == "posix"
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "geteuid")
    and hasattr(os, "memfd_create")
    and Path("/proc/self/fd").is_dir()
)
"""Whether the MCP sealed-executable launch (Linux ``memfd`` + procfs) works here."""

requires_sealed_launch = pytest.mark.skipif(
    not SEALED_LAUNCH_AVAILABLE,
    reason="MCP sealed launch needs Linux memfd + /proc/self/fd (absent on macOS/Windows)",
)
"""Skip marker for tests that need the sealed-launch mechanism to actually run."""
