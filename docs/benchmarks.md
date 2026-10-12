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

## Storage transaction and replay comparison

The four storage-dependent committed harnesses were rerun at their full default
counts against parent `a1b6fe8d` and the separated table-family/replay code, with
identical runtime dependencies. Results retain the full baseline under
`storage_comparison.baseline`, the candidate source-manifest digest and the
harness digest. Candidate measurements use a frozen parent tree with only the
storage changes, excluding concurrent uncommitted wake-adapter work.

| Measurement | Parent | Table families |
| --- | ---: | ---: |
| NORMAL append p50 (us) | 26.780 | 23.365 |
| FULL append p50 (us) | 2,933.832 | 2,932.999 |
| Short durable record p50 (us) | 8,276.162 | 8,427.158 |
| Long durable record p50 (us) | 10,807.462 | 11,014.485 |
| Read 50,000 rows (ms) | 352.756 | 388.728 |
| Replay 100,000 events (ms) | 1,759.928 | 2,304.936 |

These serial nice-19 runs share the workstation and its storage; they have no
reserved cores or discarded warm-up samples. The slower large read/replay
observations are retained, rather than described as an improvement. A single
paired run is functional regression evidence, not an isolated performance
verdict or a production throughput claim. The partial MAC index and additional
ownership/dispatch boundaries are included in the measured code.

Every existing store builds `event_row_mac_idx` at its next writable open.
That first-open build has a one-time cost; subsequent authenticated inserts
maintain the index. Older versions ignore the additive index. The read-only
projection never installs it.

`authenticated_write_benchmark.py` measures fresh MAC-enabled stores using a
fixed public fixture key and verifies every measured row. Three alternating
parent/candidate pairs (500 NORMAL and 100 FULL events each) gave these medians
of the per-run median latency, on the same shared host:

| Authenticated append | Parent p50 (us) | Table families p50 (us) |
| --- | ---: | ---: |
| NORMAL | 35.423 | 34.954 |
| FULL | 5267.937 | 5767.605 |

All raw pairs and the harness digest are retained in
`sustained_write_benchmark.json` under
`storage_comparison.authenticated_write_probe`. These observations include
index maintenance and filesystem noise and establish no isolated throughput
claim. Reproduce with `PYTHONPATH=src python benchmarks/authenticated_write_benchmark.py`.

The earlier frame-pipeline comparison is retained under
`historical_frame_pipeline` in the native-message JSON; its earlier
composition-root comparison is nested under
`historical_frame_pipeline.historical_composition_root`. Their old source
fingerprints remain attached to those measurements, not the current storage
results.

## Coordination wire baseline

`coordination_wire_benchmark.py` measures six fixed version-two baseline frames
and three representative version-three delivery frames using standard JSON
encoding and the production bounded decoder. The committed result in
`benchmarks/results/coordination_wire_benchmark.json`
records per-frame bytes and local median codec time for 1,000 iterations.
The 2026-09-19 Python 3.12.3 shared-host run measured 1,168 bytes for the
unchanged six-frame baseline and 1,498 bytes for the three new delivery frames
(request 441, offer 546, status 511). The nine frames total 2,666 bytes with
default spacing and 2,485 bytes minified. These small control
frames do not establish network throughput or justify a new wire encoding;
the [compatibility decision](protocol-compatibility-plan.md) keeps JSON.

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

| Active claims | Steady heartbeat expiry (us) | Mass expiry (us) | Non-overlap claim scan (us) |
| ---: | ---: | ---: | ---: |
| 10 | 0.788 | 15.000 | 52.032 |
| 100 | 0.750 | 82.700 | 352.664 |
| 1,000 | 0.710 | 1,117.430 | 2,864.720 |
| 10,000 | 0.956 | 22,135.310 | 30,417.658 |
| 100,000 | 0.682 | 138,498.600 | 321,395.649 |

Replay also scales with event count on the same reference run:

| Events | Replay time (ms) | Events/s |
| ---: | ---: | ---: |
| 100 | 2.028 | 49,320 |
| 1,000 | 24.389 | 41,001 |
| 10,000 | 194.883 | 51,312 |
| 100,000 | 2,304.936 | 43,385 |

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


## Socket chat and keyed lease handlers

`PYTHONPATH=src python benchmarks/coordination_handlers_benchmark.py` runs
100 receipt-requested directed chats and 20 complete keyed lease cycles through
two actual clients and an isolated durable loopback hub. Every cycle claims,
updates, checkpoints, hands off and releases; the harness verifies stored chat
and claim counts, an empty final claim set and zero pending operation outbox.

Three alternating parent/candidate pairs compare source `0184a1d8` with the
local delivery and lease-stage decomposition. Each row is the median of the
three measured per-run medians, in microseconds:

| Operation | Parent p50 (us) | Handler stages p50 (us) |
| --- | ---: | ---: |
| Directed chat receipt | 16363.081 | 19226.958 |
| checkpoint | 7635.498 | 7347.682 |
| claim | 8687.961 | 8212.138 |
| handoff | 11616.502 | 10480.377 |
| release | 7575.021 | 7834.439 |
| task_update | 8490.360 | 7558.549 |

These serial nice-19 runs share CPU and storage with other workstation sessions.
They establish functional regression evidence, without an isolated performance
claim. The slower directed-chat and release observations remain visible. Full distributions, source and harness digests, observed CPU and load
context are retained in `coordination_handlers_benchmark.json`. The earlier stage
comparison is also retained under `initial_stage_comparison`;
no observation was discarded. Existing record, store, wire and state-only
harnesses measure different unchanged paths.

The decomposition changes the two original handler modules (2,262 lines)
to five modules (2,981 lines, including native documentation and typed
stage boundaries). Shorter procedures do not imply fewer source lines.
