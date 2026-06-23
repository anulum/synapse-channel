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

`scalability_benchmark.py` profiles the one part of the hub that grows with load:
every state mutation (claim, release, heartbeat, …) lazily expires stale leases,
which scans the live claim set — an `O(active_claims)` step. The comparison count
per scan is exact (it equals the active claim count); the wall-clock time is
host-specific, so the host CPU and Python version are recorded with each result
and the **linear shape**, not the absolute times, is the reproducible finding.

On the committed reference host (Intel i5-11600K, Python 3.12) the mean
per-mutation scan and the rate at which that scan alone would saturate one core:

| Active claims | µs / mutation | Mutations/s before one core saturates |
|---:|---:|---:|
| 10 | 0.6 | ~1,600,000 |
| 100 | 2.5 | ~400,000 |
| 1,000 | 21 | ~47,000 |
| 10,000 | 254 | ~3,900 |
| 100,000 | 3,105 | ~320 |

**Reading it honestly.** At the local-first design scale — a handful to a few
dozen agents holding tens to low-hundreds of claims — the scan is **0.6–2.5 µs**,
under a millionth of a core, completely invisible. It stays negligible into the
thousands of claims. It only becomes a real ceiling around **100,000 active
claims sustaining hundreds of mutations a second** — far past what a single,
deliberately local-first hub is meant to hold. And the scan is not the binding
constraint anyway: a single process and a single event loop cap throughput long
before the expiry scan does. A heap-based expiry sweeper would turn the `O(n)`
scan into `O(log n)`, but it would optimise something that is not the limit at any
scale this design targets — so it is recorded as a tracked option, not a fix.
