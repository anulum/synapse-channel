# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — bounded run receipt for a sandboxed tool execution
"""Bounded, audit-grade evidence for one sandboxed tool: a run receipt and a preflight report.

Every sandboxed execution produces a :class:`RunReceipt` — the same kind of bounded
evidence a release receipt carries: what tool ran (id + content digest), the digest of
the input it was given, the capabilities it was granted, how it exited, the digest of its
output, and the fuel it burned. A :class:`PreflightReport` is the cheaper sibling: it
records whether a tool *could* run — its module is well-formed, its entrypoint is exported,
and it matches its manifest digest — without ever executing it, so an operator can gate a
``run --approve`` on a real pre-flight. Both are pure data built with :mod:`hashlib`; this
module names no runtime and does no execution. The runtime that produces them lives in
:mod:`synapse_channel.core.wasm_sandbox`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
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
    preopened_paths: list[str]
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
    preopened_paths: Sequence[str] = (),
) -> RunReceipt:
    """Assemble a :class:`RunReceipt` from a run's inputs, output, and outcome.

    ``preopened_paths`` records the canonical host directories the run actually preopened,
    after symlink resolution (:mod:`synapse_channel.core.sandbox_paths`) — the audit trail of
    where on the host the sandbox reached, resolved rather than as the manifest's literal
    strings. It is empty for a run that grants no filesystem, or one refused before it executed.
    """
    return RunReceipt(
        tool_id=manifest.tool_id,
        content_digest=manifest.content_digest,
        inputs_digest=digest_bytes(inputs),
        granted_capabilities=granted_capabilities(manifest),
        preopened_paths=list(preopened_paths),
        exit=exit,
        output_digest=digest_bytes(output),
        fuel_used=fuel_used,
        reason=reason,
    )


class PreflightReport(TypedDict):
    """The bounded evidence of a dry-run pre-flight, produced without executing the tool.

    Records whether the presented module is well-formed WebAssembly (``module_valid``),
    whether its ``entrypoint`` is an exported function (``entrypoint_exported``), whether
    its content digest matches the manifest the operator would run it under
    (``digest_matches``), the capabilities it *would* be granted, and a single ``ok`` verdict
    that is true only when a subsequent ``run --approve`` would at least start.
    """

    tool_id: str
    content_digest: str
    digest_matches: bool
    module_valid: bool
    entrypoint: str
    entrypoint_exported: bool
    exported_functions: list[str]
    granted_capabilities: list[str]
    ok: bool
    reason: str


def build_preflight_report(
    *,
    manifest: CapabilityManifest,
    content_digest: str,
    module_valid: bool,
    exported_functions: tuple[str, ...],
    entrypoint: str,
    compile_error: str = "",
) -> PreflightReport:
    """Assemble a :class:`PreflightReport` from a tool's compile result and its manifest.

    The presented module's ``content_digest`` is compared with the manifest's, the
    ``entrypoint`` is checked against the module's exported functions, and the first failing
    condition (in order: invalid module, missing entrypoint, digest mismatch) is recorded as
    the ``reason``. ``ok`` is true only when all three pass — the faithful gate a
    ``run --approve`` would clear.
    """
    digest_matches = content_digest == manifest.content_digest
    entrypoint_exported = module_valid and entrypoint in exported_functions
    if not module_valid:
        reason = f"module is not valid WebAssembly: {compile_error}"
    elif not entrypoint_exported:
        reason = f"entrypoint '{entrypoint}' is not an exported function"
    elif not digest_matches:
        reason = "module digest does not match the manifest (a swapped or rebuilt module)"
    else:
        reason = ""
    return PreflightReport(
        tool_id=manifest.tool_id,
        content_digest=content_digest,
        digest_matches=digest_matches,
        module_valid=module_valid,
        entrypoint=entrypoint,
        entrypoint_exported=entrypoint_exported,
        exported_functions=list(exported_functions),
        granted_capabilities=granted_capabilities(manifest),
        ok=module_valid and entrypoint_exported and digest_matches,
        reason=reason,
    )
