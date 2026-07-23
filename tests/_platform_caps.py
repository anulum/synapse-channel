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
import sys
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

PROC_AVAILABLE = Path("/proc/self/cmdline").exists()
"""Whether the Linux ``/proc`` process filesystem is present."""

requires_proc = pytest.mark.skipif(
    not PROC_AVAILABLE,
    reason="needs the Linux /proc filesystem (absent on macOS/Windows)",
)
"""Skip marker for tests that read ``/proc`` (e.g. ``/proc/<pid>/cmdline``)."""

requires_linux = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="exercises a Linux-only platform feature (systemd user services)",
)
"""Skip marker for tests of Linux-only platform features such as systemd units."""

OWNER_ONLY_SECRETS_AVAILABLE = (
    os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "geteuid")
) or os.name == "nt"
"""Whether the portable owner-only secret/directory floor works on this OS."""

requires_owner_only_secrets = pytest.mark.skipif(
    not OWNER_ONLY_SECRETS_AVAILABLE,
    reason="owner-only secret floor needs POSIX modes or Windows NT ACLs",
)
"""Skip marker for tests that need the portable owner-only floor to run."""

LOADAVG_AVAILABLE = hasattr(os, "getloadavg")
"""Whether ``os.getloadavg`` is present (POSIX; absent on Windows)."""

requires_loadavg = pytest.mark.skipif(
    not LOADAVG_AVAILABLE,
    reason="os.getloadavg is unavailable on this platform",
)
"""Skip marker for tests that require host load averages."""
