# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — deny-by-default capability manifest for sandboxed tools
"""The permission half of the sandbox — a deny-by-default capability manifest.

A sandboxed tool gets no ambient authority: it sees no filesystem, no network, and no
resources beyond what a :class:`CapabilityManifest` explicitly grants. This module is the
*policy* half — pure, I/O-free, no runtime of its own. It decides what a tool's grants
permit (:func:`authorise`) and expresses those grants as ACL rules (:func:`to_acl_rules`)
so a tool's capabilities flow through the same deny-by-default evaluator as every other
access in :mod:`synapse_channel.core.acl` — one authorisation model, not a parallel one.
It composes with, and never widens, that model.

The WebAssembly runtime that *enforces* a granted manifest lives behind the optional
``[wasm]`` extra in :mod:`synapse_channel.core.wasm_sandbox`; this module names no runtime
and runs nowhere near one. Filesystem and network grants are access decisions (a path or a
host/port is either granted or refused); resource limits (memory, fuel, wall-clock) are
numeric caps checked here and enforced by the runtime, not ACL targets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synapse_channel.core.acl import SANDBOX, AclRule

SANDBOX_TARGET_FS = "fs"
"""ACL target kind for a filesystem capability (the guest path a tool may reach)."""

SANDBOX_TARGET_NET = "net"
"""ACL target kind for a network capability (the ``host:port`` a tool may reach)."""

AUTHORISED = "authorised"
"""The reason recorded on an allowed :class:`SandboxDecision`."""


class SandboxDenyReason:
    """Why a sandbox request was refused — deny-by-default, one reason per failure."""

    DIGEST_MISMATCH = "digest_mismatch"
    FILESYSTEM_NOT_GRANTED = "filesystem_not_granted"
    WRITE_NOT_GRANTED = "write_not_granted"
    NETWORK_NOT_GRANTED = "network_not_granted"
    MEMORY_EXCEEDS_GRANT = "memory_exceeds_grant"
    FUEL_EXCEEDS_GRANT = "fuel_exceeds_grant"
    WALLCLOCK_EXCEEDS_GRANT = "wallclock_exceeds_grant"


class SandboxManifestError(ValueError):
    """Raised when a capability manifest mapping is malformed."""


@dataclass(frozen=True)
class FilesystemGrant:
    """A preopened filesystem capability: the guest path a tool may reach, read or write.

    Parameters
    ----------
    host_path : str
        Real path on the host the runtime preopens for the tool.
    guest_path : str
        Virtual path the tool sees; it never sees the host root, only this subtree.
    write : bool
        Whether the grant permits writes; read is always implied.
    """

    host_path: str
    guest_path: str
    write: bool = False

    def perms(self) -> str:
        """Return the permission token: ``read_write`` when writable, else ``read``."""
        return "read_write" if self.write else "read"


@dataclass(frozen=True)
class NetworkGrant:
    """A network capability: one specific host and port a tool may reach.

    Network is denied by default; a grant is a single ``host``/``port``, never a wildcard.
    """

    host: str
    port: int


@dataclass(frozen=True)
class ResourceGrant:
    """Bounded resources a tool may consume: memory, fuel, and wall-clock."""

    memory_bytes: int
    fuel: int
    wall_clock_ms: int


DEFAULT_RESOURCE_GRANT = ResourceGrant(
    memory_bytes=64 * 1024 * 1024, fuel=100_000_000, wall_clock_ms=5_000
)
"""A conservative default budget: 64 MiB memory, 100M fuel, 5 s wall-clock."""


@dataclass(frozen=True)
class CapabilityManifest:
    """The full set of capabilities granted to one sandboxed tool, deny-by-default.

    Parameters
    ----------
    tool_id : str
        Stable identifier of the tool the manifest authorises.
    content_digest : str
        ``sha256:<hex>`` digest of the tool's ``.wasm`` module; binds the manifest to one
        exact module, so a swapped module no longer matches.
    filesystem : tuple of FilesystemGrant
        Preopened filesystem capabilities; empty means no filesystem access.
    network : tuple of NetworkGrant
        Network capabilities; empty means no network access.
    resources : ResourceGrant
        The memory, fuel, and wall-clock budget.
    namespace : str
        Project namespace the grants are scoped to; blank matches any namespace.
    """

    tool_id: str
    content_digest: str
    filesystem: tuple[FilesystemGrant, ...] = ()
    network: tuple[NetworkGrant, ...] = ()
    resources: ResourceGrant = DEFAULT_RESOURCE_GRANT
    namespace: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable manifest, for receipts and CLI output."""
        return {
            "tool_id": self.tool_id,
            "content_digest": self.content_digest,
            "filesystem": [
                {"host_path": g.host_path, "guest_path": g.guest_path, "write": g.write}
                for g in self.filesystem
            ],
            "network": [{"host": n.host, "port": n.port} for n in self.network],
            "resources": {
                "memory_bytes": self.resources.memory_bytes,
                "fuel": self.resources.fuel,
                "wall_clock_ms": self.resources.wall_clock_ms,
            },
            "namespace": self.namespace,
        }


@dataclass(frozen=True)
class SandboxRequest:
    """What a single sandboxed run actually asks to use.

    Parameters
    ----------
    tool_id : str
        The tool being run.
    content_digest : str
        Digest of the ``.wasm`` actually presented; checked against the manifest.
    filesystem : tuple of (str, bool)
        Requested ``(guest_path, write)`` accesses.
    network : tuple of (str, int)
        Requested ``(host, port)`` accesses.
    memory_bytes, fuel, wall_clock_ms : int
        Requested resource budget; each must fit within the manifest's grant.
    """

    tool_id: str
    content_digest: str
    filesystem: tuple[tuple[str, bool], ...] = ()
    network: tuple[tuple[str, int], ...] = ()
    memory_bytes: int = 0
    fuel: int = 0
    wall_clock_ms: int = 0


@dataclass(frozen=True)
class SandboxDecision:
    """A deny-by-default decision on whether a manifest authorises a request."""

    allowed: bool
    tool_id: str
    reason: str
    granted: CapabilityManifest | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-serialisable decision, for receipts and CLI output."""
        return {
            "allowed": self.allowed,
            "tool_id": self.tool_id,
            "reason": self.reason,
            "granted": self.granted.to_dict() if self.granted is not None else None,
        }


def _under(path: str, root: str) -> bool:
    """Return whether ``path`` is ``root`` or sits inside the ``root`` subtree."""
    trimmed = root.rstrip("/")
    return path == trimmed or path.startswith(trimmed + "/")


def _filesystem_reason(
    guest_path: str, write: bool, grants: tuple[FilesystemGrant, ...]
) -> str | None:
    """Return a deny reason for one filesystem access, or ``None`` when it is covered."""
    covering = [grant for grant in grants if _under(guest_path, grant.guest_path)]
    if not covering:
        return SandboxDenyReason.FILESYSTEM_NOT_GRANTED
    if write and not any(grant.write for grant in covering):
        return SandboxDenyReason.WRITE_NOT_GRANTED
    return None


def authorise(manifest: CapabilityManifest, request: SandboxRequest) -> SandboxDecision:
    """Return the deny-by-default decision on whether ``manifest`` permits ``request``.

    Checks run in order and the first failure returns its reason: the content digest must
    match; every requested filesystem path must be covered by a grant (and writes only
    where the grant is writable); every requested host/port must be in the allowlist; and
    the requested memory, fuel, and wall-clock must each fit within the resource grant.

    Returns
    -------
    SandboxDecision
        Allowed with the granted manifest, or denied with the first failing reason.
    """
    if request.content_digest != manifest.content_digest:
        return SandboxDecision(False, manifest.tool_id, SandboxDenyReason.DIGEST_MISMATCH)
    for guest_path, write in request.filesystem:
        reason = _filesystem_reason(guest_path, write, manifest.filesystem)
        if reason is not None:
            return SandboxDecision(False, manifest.tool_id, reason)
    for host, port in request.network:
        if not any(grant.host == host and grant.port == port for grant in manifest.network):
            return SandboxDecision(False, manifest.tool_id, SandboxDenyReason.NETWORK_NOT_GRANTED)
    if request.memory_bytes > manifest.resources.memory_bytes:
        return SandboxDecision(False, manifest.tool_id, SandboxDenyReason.MEMORY_EXCEEDS_GRANT)
    if request.fuel > manifest.resources.fuel:
        return SandboxDecision(False, manifest.tool_id, SandboxDenyReason.FUEL_EXCEEDS_GRANT)
    if request.wall_clock_ms > manifest.resources.wall_clock_ms:
        return SandboxDecision(False, manifest.tool_id, SandboxDenyReason.WALLCLOCK_EXCEEDS_GRANT)
    return SandboxDecision(True, manifest.tool_id, AUTHORISED, granted=manifest)


def to_acl_rules(manifest: CapabilityManifest) -> list[AclRule]:
    """Express a manifest's filesystem and network grants as deny-by-default ACL rules.

    Each grant becomes one :class:`~synapse_channel.core.acl.AclRule` with the ``sandbox``
    permission, so a tool's capabilities are evaluated by the existing
    :func:`~synapse_channel.core.acl.evaluate_access` — the same model as every other
    access. Resource limits are numeric caps (see :func:`authorise`), not ACL targets, so
    they are not expressed here.
    """
    rules: list[AclRule] = []
    for grant in manifest.filesystem:
        rules.append(
            AclRule(
                permission=SANDBOX,
                target_kind=SANDBOX_TARGET_FS,
                target_pattern=grant.guest_path,
                namespace=manifest.namespace,
                reason=f"{manifest.tool_id} filesystem grant ({grant.perms()})",
            )
        )
    for endpoint in manifest.network:
        rules.append(
            AclRule(
                permission=SANDBOX,
                target_kind=SANDBOX_TARGET_NET,
                target_pattern=f"{endpoint.host}:{endpoint.port}",
                namespace=manifest.namespace,
                reason=f"{manifest.tool_id} network grant",
            )
        )
    return rules


def _require_str(data: dict[str, Any], key: str) -> str:
    """Return a stripped non-empty string field, raising on absence or emptiness."""
    value = str(data.get(key, "")).strip()
    if not value:
        raise SandboxManifestError(f"manifest needs a non-empty '{key}'")
    return value


def _as_list(data: dict[str, Any], key: str) -> list[Any]:
    """Return a list field, treating absence as empty and rejecting non-lists."""
    value = data.get(key, [])
    if not isinstance(value, list):
        raise SandboxManifestError(f"manifest '{key}' must be a list")
    return value


def _filesystem_from_dict(raw: object) -> FilesystemGrant:
    """Build one :class:`FilesystemGrant` from a mapping, deny-by-default on writes."""
    if not isinstance(raw, dict):
        raise SandboxManifestError("each filesystem grant must be a mapping")
    return FilesystemGrant(
        host_path=_require_str(raw, "host_path"),
        guest_path=_require_str(raw, "guest_path"),
        write=bool(raw.get("write", False)),
    )


def _network_from_dict(raw: object) -> NetworkGrant:
    """Build one :class:`NetworkGrant` from a mapping with an integer port."""
    if not isinstance(raw, dict):
        raise SandboxManifestError("each network grant must be a mapping")
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        raise SandboxManifestError("network grant 'port' must be an integer")
    return NetworkGrant(host=_require_str(raw, "host"), port=port)


def _resources_from_dict(raw: object) -> ResourceGrant:
    """Build a :class:`ResourceGrant`, falling back to the default budget per field."""
    if raw is None:
        return DEFAULT_RESOURCE_GRANT
    if not isinstance(raw, dict):
        raise SandboxManifestError("manifest 'resources' must be a mapping")

    def _positive_int(key: str, fallback: int) -> int:
        value = raw.get(key, fallback)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SandboxManifestError(f"resource '{key}' must be a positive integer")
        return value

    return ResourceGrant(
        memory_bytes=_positive_int("memory_bytes", DEFAULT_RESOURCE_GRANT.memory_bytes),
        fuel=_positive_int("fuel", DEFAULT_RESOURCE_GRANT.fuel),
        wall_clock_ms=_positive_int("wall_clock_ms", DEFAULT_RESOURCE_GRANT.wall_clock_ms),
    )


def manifest_from_dict(raw: object) -> CapabilityManifest:
    """Build and validate a :class:`CapabilityManifest` from an untrusted mapping.

    Only ``tool_id`` and a ``sha256:`` ``content_digest`` are required; every capability
    defaults to empty (deny-by-default) and resources to the default budget.

    Raises
    ------
    SandboxManifestError
        When the mapping is not an object, omits a required field, carries a digest that
        is not a ``sha256:`` digest, or holds a malformed grant.
    """
    if not isinstance(raw, dict):
        raise SandboxManifestError("manifest must be a mapping")
    content_digest = _require_str(raw, "content_digest")
    if not content_digest.startswith("sha256:"):
        raise SandboxManifestError("manifest 'content_digest' must be a 'sha256:' digest")
    return CapabilityManifest(
        tool_id=_require_str(raw, "tool_id"),
        content_digest=content_digest,
        filesystem=tuple(_filesystem_from_dict(entry) for entry in _as_list(raw, "filesystem")),
        network=tuple(_network_from_dict(entry) for entry in _as_list(raw, "network")),
        resources=_resources_from_dict(raw.get("resources")),
        namespace=str(raw.get("namespace", "")).strip(),
    )
