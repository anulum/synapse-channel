# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — owner-only secret reads: negative bounds and understated sizes
"""Refuse negative read bounds and keep the byte limit when stat understates a file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _platform_caps import requires_proc
from synapse_channel.core.secure_path import SecurePathError, read_owner_only_file_bytes


@pytest.mark.parametrize(
    ("bound", "message"),
    [
        ({"expected_size": -1}, "expected_size must be non-negative"),
        ({"max_bytes": -1}, "max_bytes must be non-negative"),
    ],
)
def test_negative_read_bounds_are_refused_before_opening(
    tmp_path: Path, bound: dict[str, int], message: str
) -> None:
    """A negative size or limit is a caller error, refused before the file is opened."""
    absent = tmp_path / "never-opened.key"
    with pytest.raises(SecurePathError, match=f"^secret: {message}$"):
        read_owner_only_file_bytes(absent, purpose="secret", **bound)


@requires_proc
def test_limit_holds_when_stat_understates_the_content() -> None:
    """procfs reports size 0 for an owner-only file; reading still stops at the limit."""
    environ = Path("/proc/self/environ")
    assert os.stat(environ).st_size == 0
    with pytest.raises(
        SecurePathError, match=f"^secret: {environ} exceeds the 1-byte secret-file limit$"
    ):
        read_owner_only_file_bytes(environ, purpose="secret", max_bytes=1)
