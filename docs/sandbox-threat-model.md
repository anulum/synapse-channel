# WASM sandbox: threat model

The `synapse sandbox` commands run an untrusted WebAssembly tool under a
capability manifest the operator approved. This page states precisely what
is contained, by which mechanism, and what is deliberately out of scope —
so an operator granting a capability knows exactly what they are granting.

The honest frame: this is a **capability-limited execution sandbox built on
wasmtime**, not a multi-tenant hostile-code isolation boundary like a
microVM. It contains a tool that might be buggy or over-eager; it is not a
claim to withstand a determined attacker exploiting a wasmtime CVE. Where
the line falls is written below, not implied.

## What is denied, and by what mechanism

Each guarantee is enforced by a *mechanism*, and each has an adversarial
test in `tests/test_wasm_sandbox_escapes.py` that drives a hostile module
past it.

| Vector | Mechanism | Failure mode |
|---|---|---|
| **Arbitrary host syscalls** | The linker defines only the WASI preview1 set. Any other import (`env::system`, a raw host function) is undefined and the module cannot instantiate. | Structural — the module fails to link (`EXIT_ERROR`, "unknown import"). |
| **Network** | The functions that would open a socket (`sock_connect`, `sock_open`) are not defined at all; `sock_recv`/`sock_send` act only on an already-open fd that no import can create, and the WASI config grants no network. | Structural — no import opens an outbound connection. |
| **Filesystem outside grants** | Only the manifest's `filesystem` grants become WASI preopens; with no grant the tool starts with no fd to any path, and a parent directory is never implicitly reachable. | Structural — the tool holds fds to exactly the granted directories. |
| **Write through a read-only grant** | Write access is opt-in per grant; a read-only grant derives a read-only preopen (`DirPerms.READ_ONLY`). | Structural — no writable fd exists for a read-only grant. |
| **Unbounded memory** | The store's memory limit is set to the manifest's `memory_bytes`; a `memory.grow` past it is refused (returns −1). | Runtime limit — the allocation never happens. |
| **Unbounded compute** | Fuel metering is on; the tool is charged per instruction against the manifest's `fuel`. | Runtime limit — `EXIT_OUT_OF_FUEL`. |
| **Wall-clock runaway** | An epoch-interruption timer trips after `wall_clock_ms`, interrupting even a tight loop that burns no fuel. | Runtime limit — `EXIT_EPOCH_DEADLINE`. |

## Attestation

Every run produces a bounded `RunReceipt` — tool id, content digest,
input/output **digests** (never the bytes), exit token, fuel used, granted
capabilities, and any containment reason. `synapse sandbox run --attest DB`
appends it to a durable event store as a `sandbox_run` event, so an auditor
can later prove which tool ran under which grants and how it exited through
the same `synapse event-query` and replay path as any coordination event —
without the tool's inputs or outputs ever entering the log.

## The gate before execution

A tool runs only when both hold: its module digest matches the approved
manifest's `content_digest` (a swapped binary is refused), and the operator
passed `--approve` (the capability grant is never implicit). `synapse
sandbox test` pre-flights the digest match and the entrypoint export
without executing anything.

## Out of scope — stated plainly

- **Side channels.** Timing, cache, and Spectre-class leakage between the
  guest and host are not addressed; do not run a tool you must isolate from
  host secrets at that level.
- **wasmtime host-escape CVEs.** The boundary is only as strong as the
  underlying runtime. Keep the `[wasm]` extra current; a runtime CVE is a
  runtime problem this layer cannot paper over.
- **Resource exhaustion of the host process.** Fuel and memory bound the
  guest; they do not bound the host orchestrator's own bookkeeping if you
  launch thousands of runs concurrently. Rate-limit at the caller.
- **Covert use of granted capabilities.** A tool granted a writable
  directory may write anything it likes there; the grant is the trust
  boundary. Grant the narrowest scope that lets the tool do its job.

## For the operator

Grant the least: the narrowest filesystem scope, the smallest fuel and
memory that complete the job, the shortest wall clock. Read the receipt —
a run that exited on a limit is telling you the grant was wrong or the tool
misbehaved. Attest runs you may need to audit. The sandbox contains an
honest tool having a bad day; the manifest is where you decide how much a
dishonest one could ever reach.
