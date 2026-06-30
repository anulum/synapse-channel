<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
-->

# WASM sandbox: getting started

This walkthrough takes one small tool from source to a capability-limited run. By the end
you will have compiled a tool to WebAssembly, written a deny-by-default permission manifest,
pre-flighted the tool without running it, and run it under the manifest with an audit
receipt — the full operator path the [sandboxed tools design](sandboxed-tools-and-marketplace.md)
describes.

The sandbox is the only place untrusted tool code runs in Synapse: a tool gets **no ambient
authority** — no filesystem, no network, no clock — beyond what its manifest grants, and the
manifest is bound to one exact module by content digest.

## Prerequisites

- The WASM runtime is a heavy dependency, so it ships behind an optional extra:

  ```bash
  pip install 'synapse-channel[wasm]'
  ```

  The `validate` verb works without it; `test` and `run` report this install hint when it is
  absent rather than failing obscurely.

- A way to produce a `.wasm` module. This guide compiles a Rust tool to the
  `wasm32-unknown-unknown` target; any language that emits a core WebAssembly module with an
  exported function works (C via `clang --target=wasm32`, TinyGo, AssemblyScript, or a
  hand-written `.wat` assembled with `wasm-tools`).

  ```bash
  rustup target add wasm32-unknown-unknown
  ```

## 1. Write and compile a tool

A sandboxed tool is a plain WebAssembly module that exports an entrypoint. The default
entrypoint name is `run`. Create `greet.rs`:

```rust
// A minimal sandboxed tool: exports `run`, takes no host authority, returns a value.
#[no_mangle]
pub extern "C" fn run() -> i32 {
    42
}
```

Compile it to a small module:

```bash
rustc --target wasm32-unknown-unknown --crate-type=cdylib \
      -C opt-level=z -C strip=symbols -C panic=abort \
      greet.rs -o greet.wasm
```

That produces a ~100-byte `greet.wasm` exporting a single function, `run`.

## 2. Write the permission manifest

The manifest binds the tool to one exact module by its `sha256:` content digest and declares
the capabilities it may use — everything is deny-by-default, so an empty `filesystem`/`network`
means none. First compute the module's digest:

```bash
python -c "import hashlib,pathlib; print('sha256:'+hashlib.sha256(pathlib.Path('greet.wasm').read_bytes()).hexdigest())"
```

Paste that digest into `greet.manifest.json` (yours will differ — the digest is unique to your
exact build):

```json
{
  "tool_id": "greet",
  "content_digest": "sha256:<paste-your-digest-here>",
  "resources": { "memory_bytes": 1048576, "fuel": 1000000, "wall_clock_ms": 2000 }
}
```

`resources` caps what the tool may consume: a memory ceiling, a fuel (instruction) budget, and
a wall-clock backstop in milliseconds. Omit `resources` to take a conservative default budget.

## 3. Validate the manifest

`validate` is a dry check of the policy alone — no tool is involved yet:

```console
$ synapse sandbox validate greet.manifest.json
manifest for 'greet' is valid: 0 filesystem, 0 network grant(s), fuel 1000000, memory 1048576 bytes
```

If the manifest is malformed (not JSON, a missing `tool_id`, or a digest that is not a
`sha256:` digest), it reports the fault and exits non-zero.

## 4. Pre-flight the tool with `test`

`test` answers *"would this tool run?"* **without running it**. It compiles the module
(validating its structure), confirms the entrypoint is an exported function, and confirms the
module matches its manifest digest — spending no fuel and executing none of the tool's code:

```console
$ synapse sandbox test greet.wasm --manifest greet.manifest.json
preflight for 'greet': ready to run
  module valid: yes
  entrypoint 'run' exported: yes
  digest matches manifest: yes
  exported functions: run
```

Because the pre-flight never executes the tool, even a tool whose entrypoint loops forever
pre-flights instantly. Ask for an entrypoint the module does not export and the pre-flight
tells you so — and exits non-zero:

```console
$ synapse sandbox test greet.wasm --manifest greet.manifest.json --entrypoint main
preflight for 'greet': NOT ready
  module valid: yes
  entrypoint 'main' exported: no
  digest matches manifest: yes
  exported functions: run
  reason: entrypoint 'main' is not an exported function
```

`test` exits `0` when the tool is ready, `1` when the pre-flight ran but the tool is not ready
(an invalid module, a missing entrypoint, or a digest that does not match its manifest), and
`2` when it could not pre-flight at all (unreadable files, or the missing `[wasm]` extra). That
makes it a safe gate in a script:

```bash
synapse sandbox test greet.wasm --manifest greet.manifest.json \
  && synapse sandbox run greet.wasm --manifest greet.manifest.json --approve
```

Add `--json` to either verb to get the machine-readable report instead of the summary.

## 5. Run the tool under its manifest

`run` executes the tool under the manifest's grants. It re-checks the module against the
manifest digest (a swapped module is refused) and requires an explicit `--approve`, so a
capability-bearing run is always an operator decision:

```console
$ synapse sandbox run greet.wasm --manifest greet.manifest.json --approve
ran 'greet' — exit ok, fuel used 2, output sha256:73475cb…3a8049
granted: resource:mem=1048576,fuel=1000000,wall=2000ms
```

Without `--approve` the run stops and tells you to confirm; with a module that does not match
the manifest digest it refuses with `digest_mismatch`. The printed line is a bounded **run
receipt** — the tool id, how it exited, the fuel it burned, the digest of its output, and the
capabilities it was granted — the same kind of audit evidence a release receipt carries. Add
`--json` for the full receipt.

## Granting access

The tool above needed nothing from the host. To let a tool read or write a directory, add a
filesystem grant — the tool sees only the virtual `guest_path`, never the host root:

```json
{
  "tool_id": "formatter",
  "content_digest": "sha256:<your-digest>",
  "filesystem": [
    { "host_path": "/srv/in",  "guest_path": "/in",  "write": false },
    { "host_path": "/srv/out", "guest_path": "/out", "write": true }
  ],
  "resources": { "memory_bytes": 1048576, "fuel": 1000000, "wall_clock_ms": 2000 }
}
```

Network is denied by construction — WASI preview1 exposes no sockets — so a `network` grant is
recorded as policy but a tool reaches the network only through a host import that is never
linked. Each grant is evaluated by the same deny-by-default ACL model as every other access in
Synapse, never a parallel one.

## Where to go next

- The [sandboxed tools and marketplace](sandboxed-tools-and-marketplace.md) design explains the
  trust chain end to end and the marketplace boundary it builds toward.
- [Signed capability cards](signed-capability-cards.md) would give a distributed tool verifiable
  provenance — a marketplace precondition.
- [Identity and ACL](identity-and-acl.md) is the deny-by-default model the sandbox reuses.
