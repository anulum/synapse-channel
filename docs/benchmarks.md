# Benchmarks

Benchmarks are runnable, committed scripts under `benchmarks/`, with their
results checked in under `benchmarks/results/`. No number here is estimated by
hand. Run them with `make bench`.

## Installed-version scorecard: `synapse benchmark`

The committed harnesses below measure this repository; `synapse benchmark`
(shipped in the package) measures *your* machine and *your* installed
version. Its probes run the same production code paths — durable event-store
appends, journal replay, lite relay encoding, and `who` plus claim-to-grant
round-trips over a real loopback WebSocket hub — and the scorecard records
the host context (CPU model, governor, load before/after) with an explicit
shared-workstation isolation label, so the output is honest regression
evidence rather than an isolated-core claim. See the
[CLI reference](cli.md) for flags (`--probe`, `--iterations`, `--json`,
`--results`).

## Relay token benchmark

`relay_token_benchmark.py` measures how much the lite relay encoding shrinks a
channel feed for a token-budgeted observer. It replays a fixed trace of broadcast
envelopes and reports three serialisations so the saving is decomposed honestly,
not quoted as one inflated figure: the full envelope on the wire, that same full
envelope minified, and the version-2 lite encoding.

On the committed 12-message trace, the lite log is **2498 of 2826 bytes (88%)**
and **807 of 919 tokens (88%)** of the raw wire form. Against the same full
envelope minified, it is **2498 of 2617 bytes (95%)**. Every payload and
auxiliary field present in the trace round-trips; timestamps are normalised to the
documented millisecond precision. The older 59% byte result counted dropped grant
and presence fields as a saving and is intentionally superseded. Byte counts are
exact; token counts use `tiktoken` when installed.

## Routing benchmark

`routing_benchmark.py` measures how the task-class router classifies a fixed
prompt set and verifies that a tiered client dispatches each prompt to the
backend for its class. On the committed 15-prompt set the split is **4 rule / 4
slm / 7 heavy**, and dispatch is verified.

Per-tier model latency needs a live model server, so it is out of the offline
scope and is documented as such rather than fabricated.

## Scalability benchmark

`scalability_benchmark.py` profiles the hub costs that grow with the amount of
work it holds:

- **Lease expiry.** Since 0.40.0, stale leases are expired through a min-heap
  keyed by lease expiry. A heartbeat over live claims checks the heap top and is
  near-constant in the claim count; a mass-expiry event drains the due heap
  entries. The benchmark describes the current heap-indexed implementation, not
  the older linear expiry model.
- **Event replay.** A hub with a durable log rebuilds state on start-up by
  replaying events.
- **Scope-conflict scan.** A new non-overlapping claim checks its file scope
  against live claims in the same worktree. That scan remains
  `O(active_claims)`.

On the committed reference host (Intel i5-11600K, Python 3.12), the current
results are:

| Active claims | Steady heartbeat expiry (µs) | Mass expiry (µs) | Non-overlap claim scan (µs) |
|---:|---:|---:|---:|
| 10 | 1.403 | 23.55 | 32.426 |
| 100 | 0.654 | 72.01 | 213.846 |
| 1,000 | 0.802 | 977.85 | 2,947.048 |
| 10,000 | 0.923 | 33,682.23 | 34,752.309 |
| 100,000 | 0.723 | 264,861.71 | 292,085.834 |

Replay also scales with event count on the same reference run:

| Events | Replay time (ms) | Events/s |
|---:|---:|---:|
| 100 | 1.345 | 74,350 |
| 1,000 | 5.953 | 167,994 |
| 10,000 | 469.33 | 21,306 |
| 100,000 | 2,848.315 | 35,108 |

**Reading it honestly.** At the local-first design scale — a handful to a few
dozen agents holding tens to low-hundreds of claims — steady lease expiry is
effectively flat, because no live lease is due. Mass expiry and durable replay
scale with the amount of work being drained or replayed. The remaining linear
hot path is the scope-conflict scan for a new non-overlapping claim; it is
measured separately so any future indexing work can be justified by data rather
than by the old, now-stale expiry model.

**Indexing decision.** Loaded workstation evidence from the committed benchmark
shows why we keep the scope-conflict scan linear inside the local-first
envelope. The decision uses a local-first ceiling of 100 active claims in one
worktree; this run measured 213.846 µs per non-overlapping probe claim at that
ceiling, below
the 10,000 µs threshold encoded in the benchmark. Operators should revisit
indexing when one worktree regularly holds 1,000 or more active claims, where
this run measured 2,947.048 µs per probe claim and the linear shape becomes
visible. This benchmark is non-isolated functional evidence, not a production
throughput claim.

## A2A bridge benchmark

`a2a_bridge_benchmark.py` measures the local HTTP+JSON bridge logic without a
network server: task creation, SYNAPSE reply correlation, task listing, push
delivery callback dispatch, and bounded subscriber fanout.

On the committed reference run (250 tasks, 32 subscribers, Python 3.12):

| Operation | Result |
| --- | ---: |
| Task creation | 5,383 tasks/s |
| Reply correlation | 12,166 tasks/s |
| Task listing | 250 tasks listed |
| Push delivery callbacks | 250 delivered |
| Subscriber fanout | 32 terminal events delivered |

**Reading it honestly.** These are in-process bridge numbers, useful for spotting
local regressions and sizing the single-process edge. They do not measure remote
HTTP server throughput, real webhook receiver latency, TLS, DNS, third-party A2A
conformance, or multi-replica behavior.

## Coding fleet benchmark

`coding_fleet_benchmark.py` measures a deterministic five-agent parallel-edit
session over the real `SynapseState` claim, scope-conflict, release, and journal
replay code. The committed scenario runs seven claim attempts: five disjoint
edits are granted and two deliberate overlapping file-scope attempts are refused.

On the committed reference run:

| Metric | Result |
| --- | ---: |
| Agents | 5 |
| Claim attempts | 7 |
| Granted claims | 5 |
| Refused overlaps | 2 |
| Conflict rate | 28.57% |
| Replay events | 10 |
| Post-release claims | 0 |

**Reading it honestly.** This is a local functional benchmark for claim
coordination evidence: conflict rate, claim latency, release cleanup, and replay
recovery. It does not measure model latency, editor integration latency, remote
coding-agent services, GitHub PR throughput, or external A2A/MCP conformance.
