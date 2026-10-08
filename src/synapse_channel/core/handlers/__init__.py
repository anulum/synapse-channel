# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — message-type dispatch registry for the hub

"""Collect handler-owned verb specs and derive the concrete hub dispatch map.

Each family owns its aliases, replies and independent guard dispositions.
The concrete Handler signature verifies structural handler capabilities against
the live hub without importing that hub at runtime. No import-time discovery or
extension execution participates in registration.
"""

from __future__ import annotations

from synapse_channel.core.handlers.attachment_peer import VERB_SPECS as ATTACHMENT_PEER_VERBS
from synapse_channel.core.handlers.attachments import VERB_SPECS as ATTACHMENTS_VERBS
from synapse_channel.core.handlers.channels import VERB_SPECS as CHANNELS_VERBS
from synapse_channel.core.handlers.dead_letter_forwarding import (
    VERB_SPECS as DEAD_LETTER_FORWARDING_VERBS,
)
from synapse_channel.core.handlers.delivery_modes import VERB_SPECS as DELIVERY_MODES_VERBS
from synapse_channel.core.handlers.entitlement_adverts import (
    VERB_SPECS as ENTITLEMENT_ADVERTS_VERBS,
)
from synapse_channel.core.handlers.federation_offer import VERB_SPECS as FEDERATION_OFFER_VERBS
from synapse_channel.core.handlers.guard_evidence import VERB_SPECS as GUARD_EVIDENCE_VERBS
from synapse_channel.core.handlers.identity_enrollments import (
    VERB_SPECS as IDENTITY_ENROLLMENTS_VERBS,
)
from synapse_channel.core.handlers.identity_pins import VERB_SPECS as IDENTITY_PINS_VERBS
from synapse_channel.core.handlers.leasing import VERB_SPECS as LEASING_VERBS
from synapse_channel.core.handlers.memory import VERB_SPECS as MEMORY_VERBS
from synapse_channel.core.handlers.message_forward import VERB_SPECS as MESSAGE_FORWARD_VERBS
from synapse_channel.core.handlers.messaging import VERB_SPECS as MESSAGING_VERBS
from synapse_channel.core.handlers.multihub import VERB_SPECS as MULTIHUB_VERBS
from synapse_channel.core.handlers.multihub_claim import VERB_SPECS as MULTIHUB_CLAIM_VERBS
from synapse_channel.core.handlers.native_message import VERB_SPECS as NATIVE_MESSAGE_VERBS
from synapse_channel.core.handlers.offerings import VERB_SPECS as OFFERINGS_VERBS
from synapse_channel.core.handlers.operator_relay import VERB_SPECS as OPERATOR_RELAY_VERBS
from synapse_channel.core.handlers.planning import VERB_SPECS as PLANNING_VERBS
from synapse_channel.core.handlers.snapshots import VERB_SPECS as SNAPSHOTS_VERBS
from synapse_channel.core.handlers.spend import VERB_SPECS as SPEND_VERBS
from synapse_channel.core.verb_registry import Handler as Handler
from synapse_channel.core.verb_registry import build_registry

VERBS = build_registry(
    (
        ATTACHMENT_PEER_VERBS,
        ATTACHMENTS_VERBS,
        CHANNELS_VERBS,
        DEAD_LETTER_FORWARDING_VERBS,
        DELIVERY_MODES_VERBS,
        ENTITLEMENT_ADVERTS_VERBS,
        FEDERATION_OFFER_VERBS,
        GUARD_EVIDENCE_VERBS,
        IDENTITY_ENROLLMENTS_VERBS,
        IDENTITY_PINS_VERBS,
        LEASING_VERBS,
        MEMORY_VERBS,
        MESSAGE_FORWARD_VERBS,
        MESSAGING_VERBS,
        MULTIHUB_VERBS,
        MULTIHUB_CLAIM_VERBS,
        NATIVE_MESSAGE_VERBS,
        OFFERINGS_VERBS,
        OPERATOR_RELAY_VERBS,
        PLANNING_VERBS,
        SNAPSHOTS_VERBS,
        SPEND_VERBS,
    )
)
"""Immutable request-to-spec registry declared beside the actual handlers."""

DISPATCH: dict[str, Handler] = {request: spec.handler for request, spec in VERBS.items()}
"""Concrete hub routing, preserving every maintained request alias."""

__all__ = ["DISPATCH", "Handler", "VERBS"]
