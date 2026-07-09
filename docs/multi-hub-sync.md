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

The full multi-host sync now routes a real claim to its owning hub and detects an ownership
partition at runtime from the peers observed asserting a namespace. The hub populates that
feed itself: `synapse hub --multihub-watch PEER=URI` (repeatable, requiring
`--namespace-owner`) runs a standing follower (`core/multihub_watch.py`) that polls each
named peer on a bounded interval and folds the observed claims into the assertions the
ownership gate consumes. Naming a peer is the operator confirmation for the always-on
outbound connection — nothing is auto-discovered — and a failed poll keeps the last
successful observation, so an outage never lets a contested namespace silently resume
granting. An operator can still wire the feed by hand (`asserting_owners` over a
follower's view) in library deployments. The
read-side CRDT layer, the cross-host event-log pull that lets one hub *observe* another over
a real connection, the **serving-side** gate that lets a hub refuse to serve its log to an
untrusted peer from the live connection, the **claim-forwarding** path that routes a claim to
its owning hub and relays the verdict, and **runtime partition detection** that refuses a
contested namespace, have shipped:

- `core/multihub_merge.py` — the conflict-free event-log union: it tags each event with
  its authoring hub, merges several hubs' logs into a grow-only set keyed by
  `(hub_id, seq)`, replays them in the deterministic `(ts, hub_id, seq)` order, and
  reports the per-hub resume cursor.
- `core/multihub_fold.py` — folds that merged order into the observed mergeable view: the
  board (last-writer-wins per task), the grow-only progress ledger, and the **observed
  claim** view — the latest claim each peer reports, tagged with its hub, marked advisory,
  cleared on release, and **never granted**.
- `core/multihub_follower.py` — a read-only `MultiHubFollower` that tracks a per-peer
  `seq` cursor, fetches a peer's events past it through an injected transport
  (`store_fetcher` reads a peer `EventStore` over its `read_since` seam), folds the union,
  and returns the observed view. Observe-only by construction: it grants no claim and,
  losing a peer, simply stops advancing that peer's cursor — the fail-closed posture. It
  is exposed to operators as `synapse multihub observe` (the walkthrough below).
- `core/multihub_wire.py`, `core/handlers/multihub.py`, and `core/multihub_transport.py` —
  the cross-host pull: a request/snapshot message pair on the hub server lets a peer ask for
  the events past a cursor, and `network_fetcher` drops a network reader into the same
  follower in place of `store_fetcher`. `core/multihub_federation.py` gates the pull
  deny-by-default (federation policy composed with mTLS peer verification), so a follower only
  pulls from a granted, cert-pinned peer. Exposed as `synapse multihub follow`.
- `core/multihub_serving.py` — the serving-side mirror of that gate. A hub configured with a
  `MultiHubServingPolicy` reads the certificate the peer presents on the **live** mutual-TLS
  connection and runs the same `authorise_multihub_peer` composition the following side
  enforces; a peer with no operator grant, no client certificate, or an untrusted pin is
  answered with an empty snapshot — the same shape as "no new events", so the refusal leaks
  nothing. A hub with no policy serves as before, so the gate is strictly opt-in.

- `core/multihub_watch.py` — the hub's own standing follower: polls each operator-named
  peer over that same pull, folds the observed claims with the gate's namespace
  derivation, and holds the per-namespace asserting-hub view partition detection
  consumes. Opt-in via `--multihub-watch`; fail-closed for authority (a failed poll
  keeps the last successful observation).

## Observing a peer — a two-hub walkthrough

The read-side layer above lets one operator *observe* another hub's coordination with no
cross-hub service running. On a single machine — or any shared filesystem — run two hubs
with separate event stores, do some work on each, and read the other's state.

### 1. Run two hubs

Each hub owns its own durable event store (`--db`):

```bash
synapse hub --port 8876 --db ./east.db &
synapse hub --port 8877 --db ./west.db &
```

### 2. Coordinate on each

Declare a task on each hub, and claim a file scope on one:

```bash
synapse task declare build --title "Build the wheel" --uri ws://localhost:8876
synapse git-claim build --paths src/ --uri ws://localhost:8876

synapse task declare docs --title "Write the docs" --uri ws://localhost:8877
```

### 3. Observe the peer

East's operator reads west's coordination, read-only, straight from its event store:

```bash
synapse multihub observe --peer-db ./west.db --peer-id west
```

```text
observing peer 'west' — 1 tasks, 0 progress notes, 0 observed claims
board:
  [open] docs — Write the docs
```

And west's operator observes east — including east's claim, which appears as an
*observed* claim, never granted locally:

```bash
synapse multihub observe --peer-db ./east.db --peer-id east
```

```text
observing peer 'east' — 1 tasks, 0 progress notes, 1 observed claims
board:
  [open] build — Build the wheel
observed claims (advisory — not granted):
  build -> <agent> @ east
```

`observe` reads the peer's event store through the same `read_since` seam the follower
uses — SQLite WAL lets it read alongside the live peer hub — and prints the folded state.
It grants nothing: a peer's claim is advisory here, and a real claim is still made on the
owning hub. Add `--json` for a machine-readable `ObservedState`.

### 4. Follow a peer over the network

When the peer is on another host with no shared filesystem, `follow` pulls its event log
over a real connection instead of reading a file. It asks the peer for the events past a
cursor and folds the same observed view:

```bash
synapse multihub follow --peer-uri wss://west.example:8876/ --peer-id west
```

```text
observing peer 'west' — 1 tasks, 0 progress notes, 0 observed claims
board:
  [open] docs — Write the docs
```

`follow` is the network counterpart of `observe`: it drops a network fetcher into the same
follower, so it grants nothing either and a peer's claim stays advisory. Pass `--token` for
a secured peer hub, `--limit` to bound the batch, and `--json` for the machine-readable
`ObservedState`. Whether a follower may pull from a peer at all is gated deny-by-default by
the federation/mTLS policy (see [Boundaries](#boundaries)); the library API
`peer_authoriser` composes it and the fetcher fails closed for an ungranted peer.

On connect, the network fetcher also reads the peer hub's advertised
`welcome.protocol_version`. A mismatch does not fail the pull: the fetcher logs an
operator-visible warning, records the negotiated lowest-common wire version for
the `MultiHubFollower`, and keeps optional features gated to that effective
version. Peers that omit the field are treated as legacy wire version `1`.

### 5. Trace causality across the hubs

With both logs at hand, `synapse causality causes ./east.db west:9 --peer west=./west.db`
answers the causality queries over the *merged* logs: events keep their global identity
`hub:seq`, the recorded relations (lifecycle, dependency, contention) are derived over the
deterministic merged order, and an edge whose endpoints two different hubs authored is
tagged `federation` with the underlying relation as its basis — so a release on one hub
that satisfied a `depends_on` claimed on another renders as `federation:dependency`. The
same honesty boundary as the fold applies: cross-hub precedence is ordered by event
timestamps across the hubs' clocks (there is no shared sequence), so a federation edge is
clock-ordered evidence, and the query observes and grants nothing. See
[the CLI guide](cli.md) for a worked cross-hub example.

### Where this stops

The cross-host event-log pull above now ships, so a hub can *observe* another over a real
connection, and the federation/mTLS gate is now enforced on **both** sides — the following
side before it pulls, and the serving side before it serves (`MultiHubServingPolicy`, see
[Boundaries](#boundaries)). What is still not built is the rest of *sync*: routing a real
claim to its namespace's owning hub.

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

The resolution behind this rule ships in `core/namespace_ownership.py`: a
`NamespaceOwnership` map records the single owning hub per namespace and resolves a
namespace to *local* (grant here), *remote* (a named peer owns it), *ungoverned*, or
*partitioned*, the last two failing closed. A hub configured with such a map enforces
it on the grant path — a claim whose namespace (derived from the agent identity, as the
ACL derives it) the hub does not own is refused with a `claim_denied` naming the owning
hub, so the caller knows where to route it; a hub with no map grants every namespace, as
a single hub does today. The networked half now ships too, opt-in: a hub configured with
`claim_peers` (a route to each owning hub) forwards a remote-owned claim over a connection
through `core/multihub_claim_transport.py`, the owner grants it on the serving side
(`core/handlers/multihub_claim.py`), and the verdict is relayed to the claimant — a grant
carrying the owner's authentic lease. An unreachable owner falls back to the refusal that
names the owner, fail-closed. Runtime partition detection ships too, opt-in: a hub wired with
an `observed_asserting_hubs` feed resolves a namespace a peer is observed contesting to
*partitioned* and refuses the claim, even on its own local grant path; `asserting_owners`
builds that feed from a follower's observed claims. What is **not** yet built is the hub
auto-populating that feed from a standing follower of its own — today the operator wires it.

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

The read-side (merge, fold, follower), the cross-host event-log pull (`observe` and
`follow`), the deny-by-default federation/mTLS gate on **both** the following and the
serving side (`MultiHubServingPolicy` reads the peer's live certificate), the
namespace-ownership resolution with its local grant-path enforcement
(`NamespaceOwnership`), the cross-hub claim forwarding that routes a remote-owned claim to its
owning hub and relays the verdict (`claim_peers` + `forward_claim` +
`handle_multihub_claim_request`), and runtime partition detection that refuses a contested
namespace when fed observed assertions (`observed_asserting_hubs` + `asserting_owners`) are
implemented — and so is the hub feeding those assertions itself from a standing follower of
its own (`core/multihub_watch.py`, opt-in via `--multihub-watch`). The design stays
deliberately conservative.

- **Claims are not a CRDT.** Mutual exclusion is not conflict-free; the design uses
  single-owner-per-namespace, not claim merging, and fails closed on an ownership
  partition. The ownership resolution, the local refusal of an unowned namespace, the
  forwarding of a remote-owned claim to its owner, refusing a contested namespace on
  observed assertions, and the hub's own standing follower feeding those assertions
  (`--multihub-watch`) all ship.
- It does **not** add a new always-on wire surface casually — the pull is a request/snapshot
  message pair on the existing hub server, reusing the event log, `read_since` seam, and
  mTLS peer bundles; it adds no always-on cross-hub service to the local core.
- It does **not** weaken local-first: a hub never depends on a peer to grant its
  own claims or run its own work.
- It does **not** introduce a global consensus cluster. There is no single global
  leader; authority is partitioned by namespace, each hub local-authoritative for
  its own.
- It makes **no multi-host claim-safety guarantee today** and changes nothing in the
  shipped single-hub runtime: the cross-host pull is observe-only.
