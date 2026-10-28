# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — retained sealed installation byte descriptors
"""Retain actual verified installation bytes in immutable kernel memory files."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from types import MappingProxyType

from synapse_channel.core.protected_write_installation_set import (
    ProtectedInstallArtifactPin,
    capture_protected_install_artifact_set,
)


@contextmanager
def retain_sealed_installation_artifacts(
    pins: tuple[ProtectedInstallArtifactPin, ...],
    *,
    expected_paths: frozenset[str],
    private_paths: frozenset[str],
    max_total_bytes: int,
) -> Iterator[Mapping[str, int]]:
    """Keep verified installation bytes sealed until supervisor preparation exits.

    Parameters
    ----------
    pins:
        Independently reviewed exact root artifact pins.
    expected_paths:
        Complete independently retained declared installation path set.
    private_paths:
        Independently retained private permission scope within that path set.
    max_total_bytes:
        Whole-set byte bound passed to actual root installation capture.

    Yields
    ------
    Mapping[str, int]
        Read-only path-to-descriptor map. Borrowed descriptors stay owned by this
        context; consumers must not close/replace them. Kernel seals refuse
        writes, resizing and further seal changes, including through duplicated
        descriptors. CLOEXEC prevents implicit inheritance across execution.

    Raises
    ------
    ValueError
        On failed installation capture or an incomplete memory-file write.
    OSError
        On unsupported non-Linux userspace, unavailable sealing, allocation,
        permission or write failures.

    Notes
    -----
    Run outside the authority actor in the trusted supervisor. This context
    captures real root artifacts before retaining bytes; it never accepts a
    caller-deserialized artifact object as installation provenance.
    It preserves byte integrity only. Dedicated UID/private proc access, allowed
    descriptor handover, complete import closure, executable entrypoint/loader,
    namespace/syscall isolation and target descriptor custody are still required.
    Sealed credentials can still be read by anyone who obtains their descriptor;
    seals are not a confidentiality capability or a quiescence receipt.
    Every owned descriptor closes on normal or exceptional exit. No account,
    persistent file, installation, process launch or authority grant is created.
    """
    if sys.platform != "linux" or not hasattr(os, "memfd_create"):
        raise OSError("protected installation sealing requires Linux memfd support")
    import fcntl

    artifacts = capture_protected_install_artifact_set(
        pins,
        expected_paths=expected_paths,
        private_paths=private_paths,
        max_total_bytes=max_total_bytes,
    )
    descriptors: dict[str, int] = {}
    try:
        for artifact in artifacts:
            descriptor = os.memfd_create(
                "synapse-protected-install", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING
            )
            descriptors[artifact.path] = descriptor
            os.fchmod(descriptor, 0o400)
            if os.write(descriptor, artifact.content) != len(artifact.content):
                raise ValueError("sealed installation artifact write was incomplete")
            fcntl.fcntl(
                descriptor,
                fcntl.F_ADD_SEALS,
                fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL,
            )
            os.lseek(descriptor, 0, os.SEEK_SET)
        yield MappingProxyType(descriptors)
    finally:
        for descriptor in descriptors.values():
            os.close(descriptor)
