# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: attachments
"""Attachments: the federation interface names of this capability.

Read an object a peer hub's owner granted, and Core's bounds and errors for objects.
Import these names from ``synapse_channel.federation_api``; this module only groups
them.
"""

from __future__ import annotations

from synapse_channel.core.attachment_store import (
    MAX_CHUNK_BYTES,
    AttachmentError,
    AttachmentStore,
)
from synapse_channel.core.attachment_transport import (
    request_attachment,
)

__all__ = [
    "AttachmentError",
    "AttachmentStore",
    "MAX_CHUNK_BYTES",
    "request_attachment",
]
