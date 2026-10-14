# Benchmarks

Benchmarks are runnable, committed scripts under `benchmarks/`, with their
results checked in under `benchmarks/results/`. No number here is estimated by
hand. Run them with `make bench`.

## Installed-version scorecard: `synapse benchmark`

The [retained-log observation](compliance.md) commands have no committed
throughput benchmark or cross-language implementation. Existing write/replay
numbers below do not measure export speed or establish its memory ceiling.
Export limits count selected events and encoded payload bytes; collected
metadata and JSON output still require linear memory.

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
the [compatibility guide](protocol-compatibility-plan.md) describes the JSON wire.
This dated fixture sample does not cover all current version-seven frames.

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

The [recorded reference artifact](https://github.com/anulum/synapse-channel/blob/main/benchmarks/results/a2a_bridge_benchmark.json)
contains 250 tasks and 32 subscribers, with host metadata declaring Python
3.12.13 on Linux. Its SHA-256 is
`1f4cb9315a7d7be7740e23fd2249939b32cfd6ec0be2e279304442f0eddcf8bf`.
These are that retained artifact's figures, not a fresh current-build result:

| Operation | Result |
| --- | ---: |
| Task creation | 4,187 tasks/s |
| Reply correlation and evidence | 3,158 tasks/s |
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
comparison, including its initial measurements, is retained under
`earlier_handler_stage_comparison`;
no observation was discarded. Existing record, store, wire and state-only
harnesses measure different unchanged paths.

### Receiver evidence comparison (2026-10-14)

Three alternating runs compare `c1d3856` with the receiver-evidence integration.
Each run uses the unchanged default 100 directed chats and 20 lease cycles.
The following values are medians of the three per-run medians, in microseconds:

| Operation | Baseline p50 (us) | Receiver evidence p50 (us) |
| --- | ---: | ---: |
| chat | 23386.356 | 18652.875 |
| checkpoint | 7801.187 | 8701.878 |
| claim | 8290.753 | 8367.819 |
| handoff | 12383.050 | 13465.151 |
| release | 8540.601 | 8955.476 |
| task_update | 8126.730 | 9389.813 |

All six actual loopback runs completed with the expected journal and operation
counts. They share CPU and storage with other workstation sessions; these
measurements support functional regression assessment without a performance
improvement claim. The JSON record retains all six distributions, exact source
and harness hashes, and the complete earlier handler-stage comparison. CPU/load
snapshots were not recorded for this comparison. The routing hub implementation
is Python; optional receipt metadata remains opaque to the other language SDKs.

The decomposition changes the two original handler modules (2,262 lines)
to five modules (2,981 lines, including native documentation and typed
stage boundaries). Shorter procedures do not imply fewer source lines.


## A2A edge, dashboard edge and pane wake

`PYTHONPATH=src python benchmarks/external_surfaces_benchmark.py` serves the
A2A bridge and the dashboard with the product's own servers on loopback and
sends 200 requests of each kind, one real TCP connection per request, each
read to its end: the agent card, an accepted message, a message refused for
its `Host` before the body is read, the dashboard snapshot document and the
classic page, the last two against a live hub. It then injects 5 wakes,
each into a new owned tmux session running a harmless composer; where tmux is
absent the result says so and no wake is measured. A request whose connection
is reset before its answer is complete is counted, not timed.

Three alternating parent/candidate pairs compare source `84a6d9f8` with the
separated handler, page, live-stream and pane-wake owners and the orderly
connection close. Each row is the median of the three measured per-run
medians, in microseconds:

| Operation | Parent p50 (us) | Candidate p50 (us) |
| --- | ---: | ---: |
| A2A agent card | 379.345 | 286.317 |
| A2A accepted message | 1043.190 | 709.921 |
| A2A refusal before the body | 397.556 | 261.321 |
| Dashboard snapshot document | 30440.062 | 29991.340 |
| Dashboard classic page | 30616.040 | 29941.900 |
| Pane wake | 821563.961 | 826091.809 |

These serial nice-19 runs share CPU and storage with other workstation
sessions. They are functional regression evidence, without an isolated
performance claim. The pane wake was the slower observation here; in an
earlier set of three pairs, retained under `earlier_set`, the refusal was. No
request of any run was reset. On Linux the parent's immediate close after a refusal
still left the client its answer, so this workload does not show the reset
that Windows clients met; that defect is covered by the connection tests, not
by these numbers. Full distributions, source and harness digests, the load
before each run and the host are retained in
`external_surfaces_benchmark.json`; no observation was discarded. The pane
wake time includes the fixed 0.4-second submit delay.
