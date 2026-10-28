# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real sealed installation descriptor lifetime tests
"""Exercise native kernel sealing and descriptor cleanup without claiming launch isolation."""

from __future__ import annotations

import os
import sys
from contextlib import ExitStack

import pytest

from synapse_channel.core.protected_write_sealed_installation import (
    retain_sealed_installation_artifacts,
)


@pytest.mark.parametrize("profile", ["darwin", "win32", "freebsd14"])
def test_unsupported_sealing_profile_refuses_before_native_access(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """Modeled platform guards refuse before kernel or installation access."""
    monkeypatch.setattr(sys, "platform", profile)
    with pytest.raises(OSError, match="requires Linux"), ExitStack() as stack:
        stack.enter_context(
            retain_sealed_installation_artifacts(
                (), expected_paths=frozenset(), private_paths=frozenset(), max_total_bytes=1
            )
        )


def test_missing_memory_file_support_refuses_before_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modeled absent memfd support refuses without installation or file access."""
    monkeypatch.delattr(os, "memfd_create", raising=False)
    with pytest.raises(OSError, match="requires Linux"), ExitStack() as stack:
        stack.enter_context(
            retain_sealed_installation_artifacts(
                (), expected_paths=frozenset(), private_paths=frozenset(), max_total_bytes=1
            )
        )
