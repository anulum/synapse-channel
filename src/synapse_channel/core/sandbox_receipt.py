# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded run receipt for a sandboxed tool execution
"""A bounded, audit-grade receipt for one sandboxed tool run.

Every sandboxed execution produces a :class:`RunReceipt` — the same kind of bounded
evidence a release receipt carries: what tool ran (id + content digest), the digest of
the input it was given, the capabilities it was granted, how it exited, the digest of its
output, and the fuel it burned. Receipts are pure data built with :mod:`hashlib`; this
module names no runtime and does no execution. The runtime that produces a receipt lives
in :mod:`synapse_channel.core.wasm_sandbox`.
"""

from __future__ import annotations

import hashlib
from typing import TypedDict

from synapse_channel.core.sandbox_policy import CapabilityManifest

EXIT_OK = "ok"
"""The tool's entrypoint returned normally."""

EXIT_OUT_OF_FUEL = "trap:out_of_fuel"
"""The tool exhausted its fuel (instruction) budget and was trapped."""

EXIT_EPOCH_DEADLINE = "trap:epoch_deadline"
"""The tool exceeded its wall-clock budget and was interrupted."""

EXIT_ERROR = "error"
"""The tool trapped for another reason, failed to instantiate, or lacked the entrypoint."""


class RunReceipt(TypedDict):
    """The bounded evidence of one sandboxed run."""

    tool_id: str
    content_digest: str
    inputs_digest: str
    granted_capabilities: list[str]
    exit: str
    output_digest: str
    fuel_used: int
    reason: str


def digest_bytes(data: bytes) -> str:
    """Return the ``sha256:<hex>`` digest of ``data``."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def granted_capabilities(manifest: CapabilityManifest) -> list[str]:
    """Return the sorted, bounded list of capability strings a manifest grants."""
    capabilities = [f"fs:{grant.guest_path}:{grant.perms()}" for grant in manifest.filesystem]
    capabilities += [f"net:{endpoint.host}:{endpoint.port}" for endpoint in manifest.network]
    capabilities.append(
        f"resource:mem={manifest.resources.memory_bytes},"
        f"fuel={manifest.resources.fuel},"
        f"wall={manifest.resources.wall_clock_ms}ms"
    )
    return sorted(capabilities)


def build_run_receipt(
    *,
    manifest: CapabilityManifest,
    inputs: bytes,
    output: bytes,
    exit: str,
    fuel_used: int,
    reason: str = "",
) -> RunReceipt:
    """Assemble a :class:`RunReceipt` from a run's inputs, output, and outcome."""
    return RunReceipt(
        tool_id=manifest.tool_id,
        content_digest=manifest.content_digest,
        inputs_digest=digest_bytes(inputs),
        granted_capabilities=granted_capabilities(manifest),
        exit=exit,
        output_digest=digest_bytes(output),
        fuel_used=fuel_used,
        reason=reason,
    )
