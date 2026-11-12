# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: record keeping
"""Record keeping: the federation interface names of this capability.

Read a journal snapshot, export its records with a commitment, and observe their
retention. Import these names from ``synapse_channel.federation_api``; this module only
groups them.
"""

from __future__ import annotations

from synapse_channel.core.record_keeping import (
    build_export,
    export_to_json,
    render_human,
    utc_event_time,
)
from synapse_channel.core.record_keeping_source import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_PAYLOAD_BYTES,
    iter_record_source,
)
from synapse_channel.core.record_retention import (
    assess_retention,
    render_retention,
    retention_to_json,
)

__all__ = [
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_PAYLOAD_BYTES",
    "assess_retention",
    "build_export",
    "export_to_json",
    "iter_record_source",
    "render_human",
    "render_retention",
    "retention_to_json",
    "utc_event_time",
]
