# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — at-rest key loading refuses paths that are not regular files
"""A directory or a FIFO in the key-file position is named a non-regular file."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from _platform_caps import requires_posix_mode_bits
from synapse_channel.core.at_rest import load_key_file


@requires_posix_mode_bits
def test_a_directory_is_not_a_key_file(tmp_path: Path) -> None:
    """A directory at the key path is refused with the regular-file reason."""
    key_dir = tmp_path / "hub.key"
    key_dir.mkdir(mode=0o700)
    with pytest.raises(ValueError, match=f"^key file is not a regular file: {key_dir}$"):
        load_key_file(key_dir)


@requires_posix_mode_bits
def test_an_owner_only_fifo_is_not_a_key_file(tmp_path: Path) -> None:
    """A FIFO never blocks the load and is refused before any read."""
    fifo = tmp_path / "hub.key"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError, match=f"^key file is not a regular file: {fifo}$"):
        load_key_file(fifo)
