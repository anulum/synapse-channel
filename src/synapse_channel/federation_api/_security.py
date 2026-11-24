# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation capability: owner-only path validation
"""Owner-only path and descriptor validation exports on :mod:`federation_api`.

This capability exposes the eight portable owner-only validation and policy
names from :mod:`synapse_channel.core.secure_path`. File/directory creators,
birth-descriptor helpers, ``core.secure_path_windows``, and ``ctypes`` types
stay off the federation surface.
"""

from __future__ import annotations

from synapse_channel.core.secure_path import (
    PortableUserKey,
    SecurePathError,
    WindowsAce,
    assert_owner_only_descriptor,
    assert_owner_only_file_path,
    current_user_key,
    evaluate_windows_owner_only_policy,
    owner_only_floor_available,
)

__all__ = [
    "PortableUserKey",
    "SecurePathError",
    "WindowsAce",
    "assert_owner_only_descriptor",
    "assert_owner_only_file_path",
    "current_user_key",
    "evaluate_windows_owner_only_policy",
    "owner_only_floor_available",
]
