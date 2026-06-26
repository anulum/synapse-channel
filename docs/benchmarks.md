# Benchmarks

Benchmarks are runnable, committed scripts under `benchmarks/`, with their
results checked in under `benchmarks/results/`. No number here is estimated by
hand. Run them with `make bench`.

## Relay token benchmark

`relay_token_benchmark.py` measures how much the lite relay encoding shrinks a
channel feed for a token-budgeted observer. It replays a fixed trace of broadcast
envelopes and reports three serialisations so the saving is decomposed honestly,
not quoted as one inflated figure: the full envelope on the wire, the same core
fields minified, and the lite encoding.

On the committed 12-message trace, the lite log is **1662 of 2826 bytes (59%)**
and **568 of 919 tokens (62%)** of the raw wire form. Holding the field set
fixed, short keys plus minification account for the lite-vs-core ratio (87%); the
rest of the reduction is the lite format dropping auxiliary fields an observer
does not need. Byte counts are exact; token counts use `tiktoken` when installed.

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
| 10 | 0.338 | 8.55 | 22.477 |
| 100 | 0.276 | 41.12 | 133.222 |
| 1,000 | 0.284 | 484.12 | 1,366.977 |
| 10,000 | 0.424 | 7,006.81 | 13,868.055 |
| 100,000 | 0.338 | 104,643.27 | 141,743.784 |

Replay also scales with event count on the same reference run:

| Events | Replay time (ms) | Events/s |
|---:|---:|---:|
| 100 | 0.854 | 117,078 |
| 1,000 | 5.29 | 189,041 |
| 10,000 | 98.92 | 101,091 |
| 100,000 | 1,128.851 | 88,585 |

**Reading it honestly.** At the local-first design scale — a handful to a few
dozen agents holding tens to low-hundreds of claims — steady lease expiry is
effectively flat, because no live lease is due. Mass expiry and durable replay
scale with the amount of work being drained or replayed. The remaining linear
hot path is the scope-conflict scan for a new non-overlapping claim; it is
measured separately so any future indexing work can be justified by data rather
than by the old, now-stale expiry model.

## A2A bridge benchmark

`a2a_bridge_benchmark.py` measures the local HTTP+JSON bridge logic without a
network server: task creation, SYNAPSE reply correlation, task listing, push
delivery callback dispatch, and bounded subscriber fanout.

On the committed reference run (250 tasks, 32 subscribers, Python 3.12):

| Operation | Result |
| --- | ---: |
| Task creation | 7,763 tasks/s |
| Reply correlation | 19,771 tasks/s |
| Task listing | 250 tasks listed |
| Push delivery callbacks | 250 delivered |
| Subscriber fanout | 32 terminal events delivered |

**Reading it honestly.** These are in-process bridge numbers, useful for spotting
local regressions and sizing the single-process edge. They do not measure remote
HTTP server throughput, real webhook receiver latency, TLS, DNS, third-party A2A
conformance, or multi-replica behavior.
