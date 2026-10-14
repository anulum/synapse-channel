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
learn* — facts, lessons, decisions, dead-ends, outcomes — as durable records with
declared evidence metadata and structural admission checks,
and exposes them on a cursored seam a downstream memory adapter reads.

The hub stays independent of external memory indexing and retrieval. Its write
handlers parse record metadata, enforce declaration floors and quotas, and
stamp the admitted origin. They do not independently verify a statement or
fetch its evidence. External memory adapters are optional.

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
job is to enforce consistency between **declared** evidence and claim metadata.
It cannot guarantee factual truth, evidence authenticity or that a producer
actually rechecked a source. A syntactically consistent false statement can
pass; downstream verification and human review keep their separate duties.

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

A pure function at the hub edge checks the parsed declarations before a finding
is journalled:

- **reject** — structurally dishonest or contradictory: missing provenance / validity / a
  required claim status or evidence basis, **or** falsified evidence claiming
  reference-validated (a direct contradiction, **LOCK-4**).
- **floor** — claimed stronger than the declared evidence category permits; the claim status (or freshness) is
  lowered and the reasons recorded, so the producer learns what was downgraded.
- **accept** — the record satisfies the structural and declaration rules.

The floors are the write-side invariants:

- **INV-1** — a `reference-validated` claim needs **both** an `evidence_ref` **and**
  `freshness == verified-at-source`. Missing declarations are floored to bounded
  support; the gate does not observe or authenticate the source recheck itself.
- **INV-2** — falsified evidence renders a claim **refuted**.
- **INV-3** — an unknown enum member is carried **opaque** and degraded read-side, never up;
  a malformed record is a structural reject. (Forward-tolerance for free.)
- **INV-6** — producer-asserted testimony cannot be born `reference-validated`, and cannot
  declare itself `verified-at-source`.

**INV-4 (honesty propagation across a synthesised recall) is read-side** — see the contract
below.

## Hub-stamped provenance — connection origin

The producing identity (`by`) and the receive-time (`at`) on every finding and recall are
stamped by the **hub** (from the connection and the clock), not taken from the message, so an
producer cannot replace these stamped fields in an admitted record. This binds
the event to the hub's resolved connection identity and clock; its strength
depends on the configured token, identity and authentication profile and the
hub's integrity. It does not validate the body, producer provenance fields or
external evidence. On cross-agent recall a
`checked_this_session == false` surfaces loudly as *"inherited testimony — confirm
independently"*.

## Durable kinds and the ingest seam

The read side ingests a subset of the durable event log, named by `MEMORY_KINDS`:

- **`finding`** — the authored atoms (the spine).
- **`recall`** — the query-stream telemetry (every lookup the fleet makes), so a read side can
  calibrate recall against the *real* query distribution rather than activity-weighted noise.
- **`checkpoint`** / **`handoff`** — the highest-signal episodic memory (resume summaries and
  ownership transfers), journalled under their own kinds.

The seam is **sequence-cursored** over admitted retained journal records.
Downstream durable processing and cursor commits must be coordinated before
advancing; retention, quarantine and storage failures remain boundaries.
The CLI output/cursor is not a downstream ingestion acknowledgement:

```python
from synapse_channel import EventStore, MEMORY_KINDS

store = EventStore("~/synapse/hub.db")
batch = store.read_since(last_seq, kinds=MEMORY_KINDS, limit=500)  # poll forward in batches
# ... process, then advance last_seq = batch[-1].seq
```

or, for an operator or a non-Python bridge:

```bash
synapse ingest ~/synapse/hub.db --memory --cursor ~/synapse/mem.cursor   # NDJSON, resumable
```

## The write-side ↔ read-side honesty contract

The write side enforces declared metadata floors. A conforming read side must
preserve them and separately verify source and content:

1. It **must not promote** a floored finding — a `traceable-unchecked` or `bounded-support`
   atom must not be surfaced as `reference-validated`.
2. A synthesised recall must render **no stronger than its weakest input** (honesty propagation,
   INV-4) — a recall built on an unverified finding is itself unverified.
3. It must surface a consumed finding whose freshness is not `verified-at-source` as inherited
   testimony to confirm, not as established fact.

The write side cannot enforce these — they are properties of *synthesis*, which happens read-side
— so they are a **contract**, not a gate. (The
producer-side invariants can be reapplied to untrusted input without certifying
that a separate synthesis implementation satisfies the complete contract.)

## Ecosystem

SYNAPSE supplies this write-side and optional participant-memory recall.
[Remanentia](https://remanentia.com) supplies independently versioned memory,
indexing and retrieval surfaces. [Director-AI](https://github.com/anulum/director-ai)
supplies response grounding; Director-Class-AI concerns action control.
The [ecosystem map](docs/ecosystem.md) distinguishes actual optional interfaces
from intended relations. Those projects are not required by the Core package,
and this page does not establish a jointly qualified verification chain.
