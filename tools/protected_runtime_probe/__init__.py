# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — immutable protected runtime prerequisite qualification
"""Prepare and qualify exact privileged prerequisites without activating a service."""


def require_native_linux(observed_platform: str, *, operation: str) -> None:
    """Require the observed Linux platform before a native probe operation.

    Parameters
    ----------
    observed_platform:
        Platform name supplied from sys.platform by the actual host adapter.
    operation:
        Fixed caller description used only in the diagnostic on refusal.

    Raises
    ------
    OSError
        Unless the observed platform is Linux. This grants no runtime authority.
    """
    if observed_platform != "linux":
        raise OSError(f"{operation} requires native Linux")
