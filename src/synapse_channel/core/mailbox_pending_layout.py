# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — durable mailbox pending counts and address hits
"""Keep the mailbox-count import path stable while each responsibility lives apart.

Schema, the startup walk, insert and delete maintenance, stored counts, and
address matching each have their own module. Callers that already import this
module keep the same names.
"""

from __future__ import annotations

from synapse_channel.core.mailbox_layout_counts import (
    count_after,
    count_between,
    load_counters,
    remember_count,
    stored_count,
)
from synapse_channel.core.mailbox_layout_maintenance import (
    record_deleted_sequences,
    record_inserted_event,
)
from synapse_channel.core.mailbox_layout_schema import (
    LAYOUT_VERSION,
    MailboxLayoutRefused,
    copy_mailbox_layout,
    ensure_mailbox_schema,
    layout_version,
    refuse_unknown_mailbox_layout,
    require_known_layout,
)
from synapse_channel.core.mailbox_layout_walk import _project_event, project_mailbox_layout

__all__ = [
    "LAYOUT_VERSION",
    "MailboxLayoutRefused",
    "_project_event",
    "copy_mailbox_layout",
    "count_after",
    "count_between",
    "ensure_mailbox_schema",
    "layout_version",
    "load_counters",
    "project_mailbox_layout",
    "record_deleted_sequences",
    "record_inserted_event",
    "refuse_unknown_mailbox_layout",
    "remember_count",
    "require_known_layout",
    "stored_count",
]
