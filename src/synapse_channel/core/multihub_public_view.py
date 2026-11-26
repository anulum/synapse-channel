# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — the public view of a journal event that multi-hub followers see
"""The public view of a journal event that multi-hub followers see.

A hub's own journal keeps private accounting on its claims: every claim-snapshot event
(:data:`~synapse_channel.core.replay.SNAPSHOT_KINDS`: ``claim``, ``task_update``,
``checkpoint`` and ``handoff``) carries
:meth:`~synapse_channel.core.state_models.TaskClaim.as_persisted_dict`, which adds the
credential-derived ``quota_principal`` and the forwarding ``via_peer`` to the public
:meth:`~synapse_channel.core.state_models.TaskClaim.as_dict` fields. A ``handoff`` may also
carry the hub's own notification routing (``_notification``). Replay and the hub's notifier
need them; no other hub does.

:func:`public_event_view` keeps only the public claim fields, as an allow-list, so a field added
to the persisted form later stays private until it is listed here. The projected kinds are
exactly the kinds replay reads as claim snapshots, so the two cannot drift apart. It is applied
at both ends of the multi-hub log:

* the owning hub serves the view (``handlers.multihub._read_snapshot``), and
* a follower applies it to every fetched event before it fingerprints or folds them
  (:meth:`~synapse_channel.core.multihub_follower.MultiHubFollower.poll`).

The view is idempotent, so a raw event from an older owner and a projected one from a newer
owner give the same fingerprint, and an upgrade raises no false equivocation. Every other
event kind passes unchanged. In particular ``ledger_task`` events, the only kind a
content-bound causal parent references by its complete-event fingerprint, are never
projected.
"""

from __future__ import annotations

from synapse_channel.core.persistence import StoredEvent
from synapse_channel.core.replay import SNAPSHOT_KINDS

__all__ = ["PUBLIC_CLAIM_FIELDS", "public_event_view"]

PUBLIC_CLAIM_FIELDS: frozenset[str] = frozenset(
    {
        "checkpoint",
        "claimed_at",
        "data_ref",
        "epoch",
        "git",
        "lease_expires_at",
        "note",
        "owner",
        "ownership_epoch",
        "path_identity",
        "paths",
        "status",
        "task_id",
        "version",
        "worktree",
    }
)
"""The fields of :meth:`~synapse_channel.core.state_models.TaskClaim.as_dict`, the public claim."""


def public_event_view(event: StoredEvent) -> StoredEvent:
    """Return ``event`` as a multi-hub follower may see it.

    Parameters
    ----------
    event : StoredEvent
        One event read from a hub's journal, raw or already projected.

    Returns
    -------
    StoredEvent
        For a claim-snapshot event, a copy whose payload keeps only
        :data:`PUBLIC_CLAIM_FIELDS`; the same object for an event that is already public or of
        any other kind.
    """
    if event.kind not in SNAPSHOT_KINDS:
        return event
    public = {key: value for key, value in event.payload.items() if key in PUBLIC_CLAIM_FIELDS}
    if len(public) == len(event.payload):
        return event
    return StoredEvent(seq=event.seq, ts=event.ts, kind=event.kind, payload=public)
