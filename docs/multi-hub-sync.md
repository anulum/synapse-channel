<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# Multi-hub sync (CRDT) research

Synapse today is single-hub authoritative: one hub owns presence, claims, the
board, and the durable event log, and every agent talks to it. This research lane
asks whether several hubs — across hosts or domains — could synchronise their
coordination state while keeping the two invariants that make Synapse worth using:
**claim safety** (two agents never edit the same file scope) and **local-first**
(a hub stays correct and usable offline for the work it owns).

The honest answer drives the whole design: most coordination state can be merged
conflict-free, but **claims cannot** — granting a lease is mutual exclusion, the
exact opposite of a conflict-free merge. So the design splits state by what is
actually CRDT-suitable and routes claims through ownership, not gossip.

## Runtime status

What exists is single-hub plus operator-managed peering, not state sync:

- One **`SynapseHub`** is the authoritative source of truth; the durable SQLite
  event log is replayed on restart so a single hub already survives crashes.
- Multi-host deployments can use **mutual TLS peer trust bundles** (see [signed
  events and mTLS](signed-events-mtls.md)) to authenticate peer hubs, and the
  [federated trust model](federated-trust-model.md) scopes which domain owns which
  project namespaces.
- The **relay log** and **`synapse ingest`** export the event stream read-side,
  which is the seam a downstream consumer (or a peer) could replay.

There is no hub-to-hub state replication and no cross-hub claim protocol. The first
slice of the CRDT layer has shipped, though: `core/multihub_merge.py` is the
conflict-free event-log union — it tags each event with its authoring hub, merges
several hubs' logs into a grow-only set keyed by `(hub_id, seq)`, replays them in the
deterministic `(ts, hub_id, seq)` order, and reports the per-hub resume cursor. It
folds no state and grants no claims; the state fold and the network follower are the
remaining slices. This document is the research boundary for that work.

## State, split by what merges

Coordination state is not one thing; each kind has a different merge story.

- **Durable event log** — append-only and the natural sync unit. Each hub's events
  carry a hub id and a per-hub monotonic sequence, so the union of two logs is a
  grow-only set ordered by `(hub_id, seq)` with vector-clock causality. Replaying
  the merged log is deterministic. This is the one piece that is genuinely
  CRDT-shaped.
- **Presence** — last-writer-wins per agent, keyed by hub id; an agent is present
  on the hub it connected to, and a peer view is advisory.
- **Progress notes and the board plan** — grow-only (notes) and LWW-per-field
  (task status), both mergeable with explicit tie-breaks by `(ts, hub_id)`.
- **Capability cards** — LWW per agent id, mergeable.
- **Claims** — **not mergeable.** A claim is a lease that must be unique per file
  scope; two hubs independently granting the same scope is precisely the collision
  the claim exists to prevent. Claims need ownership or consensus, never a merge.

## Claims without a merge

Because claims are mutual exclusion, the design routes them by **namespace
ownership**, reusing the domain model the federated trust model already defines:

- Each project namespace has exactly **one authoritative hub** at a time. Claims
  within a namespace are granted only by its owning hub, so there is never a
  conflicting grant to merge.
- A peer hub does not grant claims for a namespace it does not own; it can read the
  owning hub's claim state (advisory, eventually consistent) but routes a real
  claim request to the owner.
- Cross-namespace work that needs scopes from two owners is a coordinated, explicit
  hand-off between owners, not a silent merge.
- If two hubs ever both believe they own a namespace (a partition), the safe
  default is **refuse to grant** until ownership is re-established — claim safety
  fails closed, never open.

This keeps the strong invariant local: each hub grants claims authoritatively for
its own namespaces with no network round-trip, and only the *observed* view of
other namespaces is eventually consistent.

## Sync transport

Sync rides the existing seams rather than inventing a new protocol surface: a peer
replays another hub's event log from a cursor (the `ingest`/relay seam),
authenticated by the mTLS peer trust bundle, and folds the mergeable state in.
Because the log is the CRDT-shaped unit, "sync" is mostly "replay the peer's log
since my cursor and apply the conflict-free folds"; only namespace-ownership
changes need an explicit, operator-confirmed step.

## Local-first guarantee

Every hub stays fully usable for the namespaces it owns with no peer reachable:
claims, presence, board, and log all work offline. Sync adds an *observed* view of
peer namespaces and a merged history; it never makes a hub depend on a peer to do
its own work, and it never lets a peer grant a claim inside a namespace it does not
own.

## Boundaries

Multi-hub sync is **not implemented**. It is a research boundary, and the design is
deliberately conservative.

- **Claims are not a CRDT.** Mutual exclusion is not conflict-free; the design uses
  single-owner-per-namespace, not claim merging, and fails closed on an ownership
  partition.
- It does **not** add a new wire protocol surface casually — sync reuses the event
  log, relay/ingest seam, and mTLS peer bundles; it adds no always-on cross-hub
  service to the local core.
- It does **not** weaken local-first: a hub never depends on a peer to grant its
  own claims or run its own work.
- It does **not** introduce a global consensus cluster. There is no single global
  leader; authority is partitioned by namespace, each hub local-authoritative for
  its own.
- It makes **no multi-host safety guarantee today** and changes nothing in the
  shipped single-hub runtime.
