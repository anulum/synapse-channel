# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — live Linux protected path resolution
"""Exercise real openat2 path restrictions and retained inode comparison."""

from __future__ import annotations

import platform
from typing import cast

import pytest

from synapse_channel.core.protected_write_openat2 import verify_protected_openat2_path


@pytest.mark.parametrize(
    ("root", "parent", "leaf", "relative"),
    [
        (True, 0, None, "record"),
        (-1, 0, None, "record"),
        (2**32, 0, None, "record"),
        (0, True, None, "record"),
        (0, -1, None, "record"),
        (0, 2**32, None, "record"),
        (0, 0, True, "record"),
        (0, 0, -1, "record"),
        (0, 0, 2**32, "record"),
        (0, 0, None, None),
        (0, 0, None, ""),
        (0, 0, None, "/record"),
        (0, 0, None, "re\x00cord"),
        (0, 0, None, "nested//record"),
        (0, 0, None, "nested/../record"),
    ],
)
def test_invalid_descriptor_path_representation_refuses_before_kernel_access(
    root: object, parent: object, leaf: object, relative: object
) -> None:
    """Malformed public inputs cannot reach accidental stdio or traversal descriptors."""
    with pytest.raises(ValueError, match="invalid protected openat2 path or descriptor"):
        verify_protected_openat2_path(
            cast(int, root), cast(str, relative), cast(int, parent), cast(int | None, leaf)
        )


def test_openat2_refuses_unknown_platform_profile_before_descriptor_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A modeled unsupported architecture reaches only the refusal guard, never a fake syscall."""
    monkeypatch.setattr(platform, "machine", lambda: "unsupported")
    with pytest.raises(OSError, match="supported Linux architecture"):
        verify_protected_openat2_path(0, "missing", 0, None)
