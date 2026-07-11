<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — persistent-memory write-side architecture
-->

# The persistent-memory write-side

Synapse Channel is a coordination bus first. On top of that it ships the **write-side of an
optional persistent-memory layer**: the part that lets a fleet of agents *record what they
learn* — facts, lessons, decisions, dead-ends, outcomes — as durable, honesty-checked atoms,
and exposes them on a cursored seam a downstream memory adapter reads.

The hub stays **memory-agnostic**: it carries every memory record opaquely, never indexes or
interprets it, and runs unchanged with the memory layer switched off. The layer is an optional
edge adapter, not a hub dependency.

```
 write-side (this repo)                          read-side (a separate adapter)
 ────────────────────────                        ──────────────────────────────
 record_finding() ─► emit gate ─► durable log ─► read_since(seq, MEMORY_KINDS) ─► index ─► recall
 log_recall()     ─► (telemetry) ─┘   (hub.db)   `synapse ingest --memory`        + verify  + abstain
```

## Why a write-side gate at all

The reframing the design rests on: useful agent memory is **not** "100% total recall" — it is
*calibrated, query-weighted recall with honest abstention*. A memory that confidently returns a
stale or unsupported "fact" is worse than one that says "I don't know." So the write side's one
job is to guarantee that **every atom entering the durable log is a truthful input** — a claim
no stronger than its evidence — so the read side can reason over honest material.

## The `finding` record — three independent honesty axes

A `finding` (authored with `SynapseAgent.record_finding(...)`) places one assertion on three
*orthogonal* axes, plus provenance, a bi-temporal validity window, and a lifecycle:

| Axis | Question it answers | Members |
|------|---------------------|---------|
| `evidence_kind` | **How** is it known? | measured · curated · formally-proven · falsified · noise-limited · hardware-validated · producer-asserted · (null for decision/outcome) |
| `claim_status` | **How strong** is the standing? | reference-validated · bounded-model · bounded-support · validation-gap · external-dependency-blocked · roadmap · toolchain-gated · refuted |
| `freshness` | **How recently** was the reference re-checked at source? | verified-at-source · traceable-unchecked · untraceable |

The axes are independent (a `measured` fact can be re-checked this session or left unverified
for months) but have sensible default bindings. `freshness` is named for *recency of re-check*,
deliberately distinct from `evidence_kind`'s notion of *how* something is known.

Each finding also carries `subkind` (codebase-fact · lesson · decision · dead-end · outcome),
`evidence_ref`, `provenance {project, actor, session, …}`, `validity {valid_from, valid_to}`,
`lifecycle {active, superseded, retracted}`, and a producer-asserted `verified_at_source`.

## The emit gate — `admit(finding) → accept | floor | reject`

A pure function at the hub edge decides each atom's fate before it is journalled:

- **reject** — structurally dishonest or contradictory: missing provenance / validity / a
  required claim status or evidence basis, **or** falsified evidence claiming
  reference-validated (a direct contradiction, **LOCK-4**).
- **floor** — claimed stronger than the evidence supports; the claim status (or freshness) is
  lowered and the reasons recorded, so the producer learns what was downgraded.
- **accept** — the claims its evidence already supports.

The floors are the write-side invariants:

- **INV-1** — a `reference-validated` claim needs **both** an `evidence_ref` **and**
  `freshness == verified-at-source`; a reference that exists but was never re-checked this
  session is floored to bounded support (closing the "a ref exists, so it must be true" trap).
- **INV-2** — falsified evidence renders a claim **refuted**.
- **INV-3** — an unknown enum member is carried **opaque** and degraded read-side, never up;
  a malformed record is a structural reject. (Forward-tolerance for free.)
- **INV-6** — producer-asserted testimony cannot be born `reference-validated`, and cannot
  declare itself `verified-at-source`.

**INV-4 (honesty propagation across a synthesised recall) is read-side** — see the contract
below.

## Hub-attested provenance — non-forgeable origin

The producing identity (`by`) and the receive-time (`at`) on every finding and recall are
stamped by the **hub** (from the connection and the clock), not taken from the message, so an
agent cannot back-date or misattribute its own record. This is the fleet's
[Verified-At-Source](docs/) discipline applied at memory-emit time. On cross-agent recall a
`checked_this_session == false` surfaces loudly as *"inherited testimony — confirm
independently"*.

## Durable kinds and the ingest seam

The read side ingests a subset of the durable event log, named by `MEMORY_KINDS`:

- **`finding`** — the authored atoms (the spine).
- **`recall`** — the query-stream telemetry (every lookup the fleet makes), so a read side can
  calibrate recall against the *real* query distribution rather than activity-weighted noise.
- **`checkpoint`** / **`handoff`** — the highest-signal episodic memory (resume summaries and
  ownership transfers), journalled under their own kinds.

The seam is **sequence-cursored** over the hub's durable store, so an adapter resumes with no
loss or duplication across hub restarts:

```python
from synapse_channel import EventStore, MEMORY_KINDS

store = EventStore("~/synapse/hub.db")
batch = store.read_since(last_seq, kinds=MEMORY_KINDS, limit=500)   # poll forward in batches
# ... process, then advance last_seq = batch[-1].seq
```

or, for an operator or a non-Python bridge:

```bash
synapse ingest ~/synapse/hub.db --memory --cursor ~/synapse/mem.cursor   # NDJSON, resumable
```

## The write-side ↔ read-side honesty contract

The write side guarantees each *atom* is honest. A conforming read side must keep it that way:

1. It **must not promote** a floored finding — a `traceable-unchecked` or `bounded-support`
   atom must not be surfaced as `reference-validated`.
2. A synthesised recall must render **no stronger than its weakest input** (honesty propagation,
   INV-4) — a recall built on an unverified finding is itself unverified.
3. It must surface a consumed finding whose freshness is not `verified-at-source` as inherited
   testimony to confirm, not as established fact.

The write side cannot enforce these — they are properties of *synthesis*, which happens read-side
— so they are a **contract**, not a gate. (The
[SCPN-STUDIO federation gate](https://github.com/anulum) re-applies the producer-side invariants
on untrusted input as a reference enforcer of exactly this contract.)

## Ecosystem

This repository is the open **write-side**. The reference **read-side** adapter — the index,
the calibrated retrieval, the verification, and the honest abstention — is
[**Remanentia**](https://remanentia.com): *"Evidence memory for local AI systems … Retrieve
first. Verify next. Answer last."* Remanentia's verification step uses
**Director-Class-AI** (and the open
[**Director-AI**](https://github.com/anulum/director-ai) grounding base) to verify answers
against retrieved evidence and block unsupported outputs — the read-side enforcement of the
honesty contract above. Synapse Channel runs perfectly well without any of them; they are the
optional layer that turns honesty-checked atoms into calibrated, verified recall.
