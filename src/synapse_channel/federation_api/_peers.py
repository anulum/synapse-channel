# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — federation interface: following and merging peer hubs
"""Following and merging peer hubs: the federation interface names of this capability.

Follow a peer hub's journal, authorise and pin the peer, and merge and fold the observed
events. Import these names from ``synapse_channel.federation_api``; this module only
groups them.
"""

from __future__ import annotations

from synapse_channel.core.clock_skew import (
    ClockSkew,
)
from synapse_channel.core.federation import (
    FederationBundle,
    FederationPeer,
)
from synapse_channel.core.federation_store import (
    STORE_VERSION,
    FederationRecord,
    load_store,
    peer_from_dict,
    save_store,
)
from synapse_channel.core.multihub_federation import (
    MultiHubAuthorisation,
    MultiHubAuthoriser,
    authorise_multihub_peer,
)
from synapse_channel.core.multihub_fold import (
    ObservedBoardConflict,
    ObservedBoardProvenance,
    ObservedState,
    fold_observed_state,
)
from synapse_channel.core.multihub_follower import (
    EventFetcher,
)
from synapse_channel.core.multihub_merge import (
    HubEvent,
    merge_event_logs,
    tag_events,
)
from synapse_channel.core.multihub_transport import (
    PING_INTERVAL,
    network_fetcher,
)
from synapse_channel.core.persistence import (
    StoredEvent,
)

__all__ = [
    "ClockSkew",
    "EventFetcher",
    "FederationBundle",
    "FederationPeer",
    "FederationRecord",
    "HubEvent",
    "MultiHubAuthorisation",
    "MultiHubAuthoriser",
    "ObservedBoardConflict",
    "ObservedBoardProvenance",
    "ObservedState",
    "PING_INTERVAL",
    "STORE_VERSION",
    "StoredEvent",
    "authorise_multihub_peer",
    "fold_observed_state",
    "load_store",
    "merge_event_logs",
    "network_fetcher",
    "peer_from_dict",
    "save_store",
    "tag_events",
]
