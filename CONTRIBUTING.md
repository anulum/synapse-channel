<!--
SPDX-License-Identifier: AGPL-3.0-or-later
Commercial license available
© Concepts 1996–2026 Miroslav Šotek. All rights reserved.
© Code 2020–2026 Miroslav Šotek. All rights reserved.
ORCID: 0009-0009-3560-0851
Contact: www.anulum.li | protoscience@anulum.li
SYNAPSE CHANNEL — contribution guide
-->

# Contributing to SYNAPSE CHANNEL

Thank you for considering a contribution. This guide covers how to set up a
development environment and the standards a change must meet to be merged.

## Python support and tooling

The package and contributor environments require Python 3.11 or newer. Mypy
checks the supported Python 3.11 floor. Ruff retains its established py310
syntax and style target so a runtime support change does not require unrelated
source modernisation. That conservative style target does not provide Python
3.10 runtime support; package metadata and CI define the supported runtime.
Ruff rules and the complete coverage gate remain unchanged.

## Getting started

1. Fork and clone the repository.
2. Create a virtual environment and install the package with its dev toolchain:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -e ".[dev,benchmark]"
   ```

3. Install the git hooks so style and hygiene checks run on every commit:

   ```bash
   make install-hooks
   ```

   A commit that stages Python or `pyproject.toml` runs strict mypy across the
   whole configured tree. Expect this correctness gate to add tens of seconds;
   it deliberately ignores staged-file narrowing. The hook prefers the
   repository `.venv` on POSIX or Windows. If the dev environment lives
   elsewhere, set `SYNAPSE_MYPY_PYTHON` to its absolute Python path; an invalid
   override fails closed.

   The hooks are mandatory. When every `pre-commit` hook has passed, the
   `commit-msg` hook adds `Hooks-Verified: pre-commit sha256:<16 hex>` above the
   `Seat:` trailer. The value is a digest of the staged `.pre-commit-config.yaml`.
   The pre-push hook and the `commit-trailers` CI workflow refuse any new commit
   without a stamp that matches its own hook configuration. So a commit made
   with `--no-verify`, with `git commit-tree`, or from a worktree whose hooks
   are not installed cannot be pushed. Run `make install-hooks` in every
   worktree, and never land commits through a temporary index.

   A commit message is public, so the same hook also reads its subject and
   body. It refuses attribution trailers that a tool adds by default
   (`Co-Authored-By`, `Claude-Session`, "Generated with …") and internal
   coordination identifiers: agent seat ids, review or handover record names
   and hub event citations. Keep that provenance in the private coordination
   records. Product names in prose, such as the name of an integration, stay
   allowed. The history audit applies this rule to commits made after it was
   introduced. Planning codes are not matched, because their set changes with
   every programme; keep them out of public messages by reading each message
   before you commit.

   The installed pre-push hooks are deliberately lightweight. They check the
   generated capability snapshot, commit-trailer history, and version surfaces
   in seconds. They do not run pytest, coverage, or `tools/preflight.sh`; CI owns
   exhaustive tests for ordinary pushes. Run the exhaustive script locally only
   when the current task explicitly reserves resources for it or for an
   owner-authorised release verification.

## Development workflow

The `Makefile` wraps the common tasks (`make help` lists them):

| Target | What it does |
| --- | --- |
| `make lint` | ruff lint + format check |
| `make fmt` | auto-fix lint and apply formatting |
| `make typecheck` | strict `mypy` |
| `make test` | the test suite with the coverage gate |
| `make cov` | tests with a per-line missing-coverage report |
| `make reuse` | SPDX/REUSE licensing compliance |
| `make preflight` | exhaustive opt-in local gate when resources are explicitly reserved |

## Complete CI shards

The Linux matrix runs four indexed pytest shards for each supported interpreter,
with at most twelve matrix jobs running concurrently. Pytest collects the full
current inventory before scheduling whole modules, longest estimated duration
first, onto the least-loaded worker. Modules retain their shared fixtures.
Every runner uses one identical immutable timing snapshot selected from the
latest successful CI run of a push to main; pull-request and manually
dispatched runs never seed it. Missing history uses deterministic current
test counts; new tests always enter collection and removed modules stay absent.
Malformed or nonfinite timing data fails before test execution. Timing history
controls placement only and never acts as a test allowlist.

Every runner keeps the same native backends and interpreter-specific host
prerequisites. Shard zero also executes the explicit Linux metadata-refusal
follow-up; each interpreter runs it once.

`tools.pytest_shard` records the complete inventory, selected IDs and actual
completed outcomes, bound to the source SHA, run, interpreter and configuration.
`tools/ci_shard_artifacts.py pack` refuses unsuccessful or incomplete receipts.
Only successful shards publish authoritative indexed artifacts. `test-complete`
requires every shard, verifies identical interpreter inventories and an exact
union without omissions or duplicates, combines actual Python 3.12 branch and
subprocess coverage, then applies the whole-package 99.36% threshold.
A shard has no individual coverage floor because it measures only its partition.
Missing, failed, skipped, duplicate, foreign or altered artifacts fail the gate.
A failed-job rerun keeps this run's successful shard artifacts and its original
timing snapshot. The aggregate accepts those earlier attempts from the same run
and still refuses a newer attempt, a different run, or an incomplete shard.
The required `ci` job includes both the matrix and the complete-union gate.

Focused runner/artifact checks live in `tests/test_ci_shard_artifacts.py` and
`tests/test_pytest_shard_schedule.py` and use real pytest and coverage subprocesses.
The aggregate also checks the common timing snapshot and publishes newly observed
module durations only after the complete coverage gate passes. Hosted timing comparisons must include
setup and queue time; sharding alone is not evidence of measured improvement.

The advisory cross-OS workflow also runs the complete default macOS collection
in four module shards. All workers use one immutable snapshot from the latest
successful cross-OS run of a push to main and retain the same locked dependencies, released-hub fixtures and
native foreign-owner fixture. Its original `cross-os-filesystem (macos-latest)`
check always aggregates the workers and fails on any failed, skipped, absent,
duplicate or foreign shard. `tools/ci_portable_shards.py` independently verifies
the complete union and binds actual platform, interpreter, source, run attempt,
configuration and shared timings. Existing test-level platform skips retain their
original meaning. This portable result does not supply Linux native-follow-up or
coverage evidence. The Windows focused pack and its check name remain unchanged.

`tests/test_ci_portable_shards.py` exercises real portable runner/artifact journeys
and the aggregate shell gate. Local Linux qualification does not certify native
macOS behaviour; a successful hosted macOS run is required for that result.

## Standards a change must meet

A test must close every file, socket and child process it opens. The pytest
settings in `pyproject.toml` turn `ResourceWarning` and
`pytest.PytestUnraisableExceptionWarning` into errors, so a leak fails the test
that caused it, locally and in every CI job. For warnings raised while tests
are collected and run, this is equivalent to passing
`-W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning`;
a command-line `-W` also covers warnings raised when the interpreter starts.
`tests/test_pytest_leak_warnings.py` proves the setting with a real leaking probe.

The CLI release compatibility tests run the complete published 0.48.0 and
0.99.27 hubs. Their missing exact-confirmation extension refuses a fresh manual
release without mutating the claim; legacy lock cleanup still works. Prepare
both hash-pinned wheels before running the focused tests:

```bash
python -m pip download --require-hashes --no-deps --only-binary=:all: \
  -r .github/requirements/requirements-legacy-release.txt \
  --dest .pytest_cache/legacy-release
python -m pip download --require-hashes --no-deps --only-binary=:all: \
  -r .github/requirements/requirements-release-admission.txt \
  --dest .pytest_cache/legacy-release
```

The test fixture verifies the wheel again and extracts it into a temporary
subprocess profile. It uses the existing dev dependencies and never installs
the old release over the candidate. For a preloaded wheel directory, set
`SYNAPSE_LEGACY_WHEEL_DIR`. A missing or altered wheel fails the test prerequisite;
it does not skip the journey. This historical protocol fixture is not a
deployment recommendation or a general minor-version compatibility guarantee.

The POSIX adapter-file ownership test also requires
`SYNAPSE_TEST_FOREIGN_OWNER_FILE`: a regular file owned by a different UID,
directly inside a safe directory owned by the test user. Its contents are never
opened or changed. The test requires the exact leaf-owner refusal; an earlier
parent-directory refusal does not qualify. For focused local runs, point this
variable at an existing native fixture. A missing or unsuitable fixture fails
the prerequisite instead of skipping the case.

The Linux full-suite, macOS full-suite and OpenCode integration jobs prepare an
inert root-owned file in a new private runner-owned temporary directory through
`.github/actions/native-file-owner`. Only that CI setup changes the new file's
owner; tests never invoke privilege tools. Windows uses its separate DACL
qualification and does not borrow a POSIX UID result.

The explicit Linux metadata-refusal profile requires `libseccomp.so.2` and a
kernel that permits an unprivileged process to install a seccomp filter:

```bash
python -m pytest tests/fixtures/opencode/metadata_denial_linux.py
```

It confines only a disposable child process, where real `fchmod` calls return
`EPERM`. Both existing and absent configuration targets must survive the refused
publication, with no leaked descriptor or temporary file; the parent must still
publish successfully. Missing native capability fails this profile. The Linux
matrix and OpenCode integration jobs collect it explicitly; the Python 3.12
coverage lane appends its real subprocess coverage before uploading reports.
The filename keeps Linux kernel requirements out of default macOS and Windows
collection, without adding a conditional skip or claiming cross-platform proof.

The protected filesystem profile similarly requires a native 64-bit Linux
`x86_64` or `aarch64` userspace with `openat2`, procfs and inotify:

```bash
python -m pytest tests/fixtures/protected_paths_linux.py
```

It exercises actual kernel path resolution, mount and symlink refusal,
absent-leaf checks, descriptor cleanup and independent processes changing file
bytes, metadata or parent attachments. The former device-mismatch and
concurrent-inspection cases live here with native observations. Linux CI
collects the complete profile together with `tests` on every supported Python
version, preserving both default filename patterns and adding this profile:

```bash
python -m pytest tests -o 'python_files=test_*.py *_test.py protected_paths_linux.py'
```

Python 3.12 measures that combined population before evaluating its
coverage floor, so native implementation is included in the first report.
Missing native prerequisites fail this profile. Default collection retains
portable descriptor/input checks and
the existing POSIX platform guards; it does not collect these Linux-only cases
on macOS or Windows. The modeled architecture-refusal test proves only the
guard's rejection behaviour, while the explicit profile proves Linux behaviour.

Protected authority peer authentication requires Linux `SO_PEERCRED`. The
shared `requires_linux_peer_credentials` marker in
`tests/protected_write_socket_examples.py` enrolls only tests that require that
native credential backend, and its conditional guard is tracked in
`tests/test_coverage_exclusion_ledger.py`. Invalid-policy cases and signed TCP
status remain in every platform inventory.
`tests/test_protected_write_socket_platform.py` exercises actual unsupported-host
refusal before accept callbacks, tasks or listening sockets, and a real portable
Unix stream uses a short relative path beneath a deliberately long private
pytest directory. Only that POSIX asyncio stream case excludes Windows.
These checks use the actual host; Linux runs do not certify macOS or Windows.

Installation capture and sealed-byte custody have three dedicated Linux profiles:

```bash
python -m pytest tests/fixtures/protected_install_artifacts_linux.py
python -m pytest tests/fixtures/protected_installation_set_linux.py
python -m pytest tests/fixtures/protected_sealed_installation_linux.py
```

They read public root-owned OS configuration and executable files through their
actual descriptor ancestry. These files exercise artifact capture mechanics;
they are not a complete application installation or secret-provisioning fixture.
The sealing profile requires Linux `memfd_create` and file seals. Actual duplicated
descriptors, disposable readers, close-on-exec and a child file-size limit test
byte integrity, handover and cleanup. Tests never install accounts, alter OS files
or use real credentials. Missing native prerequisites fail these profiles.
Linux CI adds their exact filenames alongside `protected_paths_linux.py` to the
two default pytest filename patterns, measuring the complete native population
in the initial coverage invocation. Default collection keeps portable enrollment
and modeled unsupported-platform guards; those guards are not foreign-OS proof.

The cgroup, mount and process-subtree profiles require native Linux cgroup v2,
a working systemd user session, procfs and Bubblewrap user namespaces:

```bash
python -m pytest \
  tests/fixtures/protected_write_cgroup_controls_linux.py \
  tests/fixtures/protected_write_cgroup_parsing_linux.py \
  tests/fixtures/protected_write_kernel_boundary_linux.py \
  tests/fixtures/protected_write_kernel_capture_races_linux.py \
  tests/fixtures/protected_write_mount_attributes_linux.py \
  tests/fixtures/protected_write_mounts_linux.py \
  tests/fixtures/protected_write_subtree_linux.py \
  tests/fixtures/protected_write_subtree_membership_linux.py \
  tests/fixtures/protected_write_subtree_stop_linux.py
```

They capture real retained descriptors, migration controls and namespace mount
tables, and terminate only disposable owned process groups. Their transient user
services have bounded lifetimes and resource limits. The Linux matrix prepares
the runner's user bus and collects every profile in each indexed interpreter
inventory; Python 3.12 includes their coverage in the complete 99.36% union.
On disposable GitHub-hosted Linux runners, the native-session composite action
loads a Bubblewrap-only AppArmor profile admitting user namespaces while leaving
the global restriction unchanged. Before tests, a delegated unprivileged process
must demonstrate separate user, mount, PID and network namespaces, a private
procfs, a read-only root and a functioning isolated loopback. Missing capabilities
fail setup. The profile is an ephemeral CI prerequisite, not a host installation
recipe; the action refuses other environments.

Native subprocess coverage is appended only when that shard produced native
coverage files. A shard without such observations still requires its main
coverage database, successful shard receipt and output directory. The coverage
runner gives every native writer case a fresh SHA-256 node-ID directory before
fixture setup. Each selected case must supply its own valid branch coverage
file before any data is merged; extra observations from one case cannot replace
a missing observation from another. Unassigned or invalid files fail the merge.
The complete interpreter inventory and
99.36% union gate remain mandatory; an empty native directory does not represent
native qualification.
Missing prerequisites fail the profiles. Default collection retains both
portable filename patterns and excludes these explicitly named Linux profiles
on other operating systems. Successful observations do not certify a dedicated
installed authority, migration exclusion or recovery custody.

- **Tests.** Every new module, function, and branch ships with tests. Aggregate
  coverage stays at or above the 99.36% floor; a change must not lower it.
  [Checking the coverage figure](docs/coverage.md) shows how to read the
  measured figure of a CI run and how to measure it again.
- **Test taxonomy.** Reserve the `e2e` name and real-runtime CI mental
  model for journeys that exercise a live process path (CLI subprocess,
  hub socket, or `real_hub`). Documentation and design-doc guards use
  `pytest.mark.docs_contract` (see `tests/*_design_docs.py`). Do not name
  a docs-only guard `*_e2e_*`. CI prints an advisory inventory via
  `python tools/report_test_taxonomy.py` (counts only; never a red gate).
- **Coverage exclusions are ledgered.** Every `pragma: no cover` in `src/` and
  every conditional skip in `tests/` is enumerated with a justification class
  in `tests/test_coverage_exclusion_ledger.py`; the suite fails the moment the
  tree and the ledger disagree, so adding one is a deliberate, reviewed edit.
  Unconditional skips and xfails are not accepted.
- **Types.** `mypy` runs in strict mode and must pass with no new ignores.
- **Style.** Code is formatted with `ruff format` and linted with `ruff`
  (`E, F, I, B, UP, D` with the NumPy docstring convention). Public symbols carry
  NumPy-style docstrings.
- **Licensing.** Every new file carries the SPDX header block (see any existing
  file); `make reuse` must stay clean.
- **Single responsibility.** One module is one responsibility; split rather than
  grow a module that does two things.
- **No fabricated data.** Every benchmark number in the docs comes from a
  committed, runnable script under `benchmarks/` with results checked in.
- **Changelog.** A user-visible change adds a `CHANGELOG.md` `[Unreleased]`
  fragment under the right heading (`Added`, `Changed`, `Fixed`, `Security`).
- **Backward compatibility.** Current `0.x` releases do not promise backward
  compatibility across minor releases. A change to the wire protocol, a CLI
  flag's meaning, or a public function's signature states its compatibility
  impact in the commit or PR, updates its frozen contract, and adds changelog
  plus migration notes. A wire-incompatible change also bumps
  `WIRE_PROTOCOL_VERSION`. Starting with `1.0.0`, a breaking stable public
  Python API change requires a package major release. See
  [`docs/api-stability.md`](docs/api-stability.md).
- **Threat-model delta.** A change that touches authentication, ACLs, exposure
  guards, TLS, rate limiting, or the durable log states what it changes in the
  posture and updates [`SECURITY.md`](SECURITY.md) or
  [`docs/paranoid-mode.md`](docs/paranoid-mode.md) when a control's contract moves.

Five additional Linux profiles exercise durable protected execution:

```bash
python -m pytest \
  tests/fixtures/protected_write_descriptor_filter_linux.py \
  tests/fixtures/protected_write_sequence_linux.py \
  tests/fixtures/protected_write_execution_frontier_linux.py \
  tests/fixtures/protected_write_observation_effect_faults_linux.py \
  tests/fixtures/protected_write_observation_topology_faults_linux.py
```

The descriptor-filter profile requires native Linux x86-64, seccomp and procfs.
Irreversible filters run only in disposable children; real ancillary transfers,
thread synchronization, `pidfd_getfd` denial and inherited filters are checked. The counter profile
uses actual process/thread contention, restart, file replacement and child-only
file-size limits. A descriptor-specific libseccomp rule produces a real child-only
`fsync` refusal without substituting a syscall. Checksummed counter records reject real partial updates and
stored mixed carry bytes rather than issuing a reused sequence. The remaining profiles use real SQLite journals and native
`openat2` filesystem observations to reject incomplete or inconsistent execution
evidence. Their fixtures exercise storage mechanics, without certifying installed
writer isolation or quiescence. Missing native capabilities fail these profiles.
Linux CI collects all five exact filenames alongside the existing two default
filename patterns on Python 3.11–3.13; Python 3.12 includes their real subprocess
coverage. They are outside default macOS and Windows collection.

### Kernel-checked execution and authority IPC profiles

Three Linux profiles exercise the composed writer entry point and its actual
authority transport:

```bash
python -m pytest \
  tests/fixtures/protected_write_kernel_execution_linux.py \
  tests/fixtures/protected_write_filtered_status_ipc_linux.py \
  tests/fixtures/protected_write_prebegin_race_linux.py
```

They require Linux cgroup v2, procfs, `openat2`, Unix peer credentials and,
for the filtered writer, the Linux x86-64 seccomp backend. The composition
profile verifies refusal before a status request, execution consumption or
filesystem effect when root scope, evidence scope, root identity or the live
kernel boundary differs. The transport profiles use real processes, signed
WebSocket messages and durable authority state to observe enrollment revocation
and refuse a delayed begin after revocation. These component checks do not
certify a dedicated installed writer, revocation fencing or recovery custody.

Both Linux interpreter commands collect the exact profile filenames alongside
the existing inventory; Python 3.12 includes their actual subprocess coverage
in the complete 99.36% gate. Missing prerequisites fail the profiles.
Default macOS and Windows collection does not include these native profiles.

### Protected frontend control tests

The private protected frontend channel requires native Linux `SO_PEERCRED`.
Its portable policy, codec and authenticated-session tests remain collectable
on other platforms. Native cases reuse the existing peer-credential marker:

```bash
python -m pytest tests/test_protected_write_request_session.py
python -m pytest tests/test_protected_write_control_policy.py
python -m pytest tests/test_protected_write_control_wire.py
python -m pytest tests/test_protected_write_control_server.py
python -m pytest tests/test_protected_write_control_client.py
python -m pytest tests/test_protected_write_control_gateway.py
python -m pytest tests/test_protected_write_handler_binding.py
```

The gateway journeys spawn and reap an actual separate frontend process with
no authority key or journal argument. They exercise two external WebSocket
clients, all seven protected verbs, signed request/result binding, raw ambiguous
JSON, unsigned and ordinary-frame refusal, revocation and shared durable replay.
Native control tests check actual PID/UID/GID mismatch, foreign connection and
epoch selectors, sequence refusal, bounded reservations and partial-frame
deadlines. A separate native SQLite writer lock delays a real journal commit
while the tests cancel a client or close the control server. The server must
drain that admitted mutation before returning, with no automatic replay.

These are same-UID diagnostic processes with private temporary sockets, not
installed dedicated identities. They create no host accounts, production
listeners or replacement actors, and do not prove isolated signer custody or
the supervisor's prior-actor termination gate.

### Known-state recovery profiles

The recovery handler's authenticated loopback and key-conflict journeys use
`tests/test_protected_write_recover_handler.py` and
`tests/test_protected_write_recover_conflict.py`. Six additional Linux profiles
exercise real native observations and immutable storage:

```bash
python -m pytest \
  tests/fixtures/protected_write_recovery_snapshot_linux.py \
  tests/fixtures/protected_write_recovery_evidence_linux.py \
  tests/fixtures/protected_write_recovery_candidate_linux.py \
  tests/fixtures/protected_write_recovery_admission_inputs_linux.py \
  tests/fixtures/protected_write_recovery_topology_faults_linux.py \
  tests/fixtures/protected_write_recovery_escaped_descriptor_linux.py
```

They require native Linux `openat2`, procfs and the current descriptor backend.
The descriptor-transfer profile additionally requires Linux x86-64 seccomp,
cgroup v2 and a working systemd user session. It owns a transient resource-bounded
service and two disposable processes, comparing a real blocked transfer with an
outside descriptor holder that remains able to alter the file after the writer
group is killed. Its cleanup terminates and reaps every owned child. An empty
group and a frozen packet do not prove current filesystem custody.

Both complete Linux interpreter inventories include all six exact filenames
alongside the two existing default patterns and all earlier native profiles.
Python 3.12 includes their actual subprocess coverage in the complete
99.36% gate. Missing prerequisites fail; these profiles add no conditional skips
and stay outside default macOS/Windows collection. Passing component journeys
qualify observation, binding and refusal mechanics, not an installed recovery
service or separate-principal writer isolation.

### Finite Linux runtime qualification

`python -m tools.protected_runtime_probe.prepare --help` describes ordinary-user
packet preparation. It freezes the current source, WebSockets 17.4 dependency,
licences and native inputs with exact byte pins. Review the manifest and
bootstrap hashes independently before considering an execution attempt.

Licence inputs must be regular files within the capsule's per-file byte bound.
Capture rejects symlinks at the licence file or its immediate parent, and rejects
nonregular files before staging. Stable ordinary cache hardlinks remain supported.

The printed command is **guest-side only** and contains no `sudo`. It requires
one independently reviewed disposable Linux guest with one virtual CPU. The
manifest binds a new UUID for that attempt. Before creating backing, accounts,
mounts or cgroups, the pinned bootstrap checks that exact native DMI UUID, the
hypervisor CPU flag and the complete initial-user-namespace UID mapping. A
mapped-root user namespace or an ordinary host refuses before setup. A packet
or a successful identity check grants no launch authority; the complete guest
launcher, readonly seed, private disks, evidence return and resource envelope
must also pass independent review.

All four dedicated accounts, `/opt` directories, mounts, cgroups and run backing
exist **inside that guest only**. Its private disk supplies the guest mount
`/media/anulum/GOTM`; this path is never a host share. Guest poweroff ends active
processes, mounts and cgroups. Account metadata and retained backing remain in
the held guest disk. Host artifacts are the disk, readonly seed and returned raw
evidence and logs. Keep them until owner-authorized retirement; no automatic
deletion or workstation account, `/opt`, mount or cgroup change is part of this
workflow. Failed cleanup remains a hold requiring inspection.

If a readonly remount and its subsequent detachment both fail, the bind can
remain writable until guest shutdown. Both failures remain in the exception
chain. Treat that view as unqualified: do not start the controller from it, and
retain the guest disk and failure logs for inspection.

The default packet is explicitly a **host-tools prerequisite** profile. The
captured-runtime profile additionally requires all four inputs:
`--runtime-image`, `--runtime-manifest`, `--runtime-sha256` and
`--dependency-wheel`. They identify an independently pinned complete native
runtime and its original WebSockets wheel. Every native object must match the
observed import closure and its system-package or original-wheel provenance;
a missing captured executable never falls back to host Python.

To record that captured profile, also supply both `--instrumentation-root`
and `--instrumentation-wheel`. The root names the `coverage` package inside a
complete original Coverage 7.17.0 wheel tree. The preparer verifies its full
RECORD, native tracer, licences and source bindings and adds the explicit
`--instrumentation` selector to the operator command. It records six process
contexts: controller, namespace wrapper, authority, client, operator and writer.
Recording tightens the controller's `RLIMIT_FSIZE` soft and hard limits to at
most 16 MiB; every role child inherits that bound. The report records the actual
controller limits. The authority also uses a SIGTERM graceful stop only in this
instrumented profile. EFBIG and SIGXFSZ are graded as instrumentation **ERROR**,
with no product-result claim. These resource and shutdown differences must stay
with exported evidence; they are not an uninstrumented performance comparison.

Each process serializes only its own in-memory branch data into its enrolled
output. The parent seals raw datasets only after successful producer exits and
quiescence; privileged code never parses a child's SQLite database.
Failed collection is terminal. Inspect failure-retention metadata before closing
the campaign. Closing invalidates every resource accessor before cleanup starts,
including when saving fails, and repeated closure does not touch reused descriptors.

Use `python -m tools.protected_runtime_probe.instrumentation_analysis --help`
for ordinary-user analysis of the exact report, capsule, sealed dataset copies
and byte-identical current source. Analysis checks every association before
parsing private working copies and refuses an existing output directory. It
preserves the original sealed inputs and reports registered files separately
from files with executed arcs. Bootstrap admission, readonly recapture,
collector startup and final sealing/publication are explicitly unmeasured.

Two ledgered pragma sites in the fixed child launcher cover three source lines:
the collector context header, its nested call, and the unreachable normal return.
The header is executed; excluding the block is necessary to cover the call at
the collector transition. Tests pin both fixed target exits and their exact
choices, and verify real target recording and exit propagation through the final
save. Scoped coverage reports retain these exclusions explicitly.

Store analysis inputs under a private (`0700`) directory. Each sealed dataset
copy must have mode `0444`, including copies extracted from an evidence archive.
Adjust only the copies' permissions; preserve their bytes and the original
archive.

These results qualify finite prerequisite and recording mechanics. They do not
install a service or writer, issue a protected-operation grant, certify recovery
or revocation fencing, establish whole-runtime acceptance or replace performance
evidence. Run the owning `tests/test_protected_runtime_probe_*.py` tests for
ordinary API, CLI and refusal contracts; successful ordinary tests alone do not
establish dedicated host-role isolation.

The [qualification evidence contract](docs/coordination-spec.md#finite-runtime-qualification-evidence)
keeps host prerequisites, captured runtime, instrumented execution, ordinary
analysis and portable observations separate. In particular, the complete
application coverage gate does not qualify the probe tools' coverage, and
source-bound observations cannot be transferred to changed source or another
platform. The [security policy](SECURITY.md#threat-model-and-posture) also states
the trusted writer's symmetric authority-key boundary.

The ordinary role-boundary tests use private account files and single-UID user
namespaces; they do not create host accounts. Setting `SYNAPSE_TEST_COVERAGE_DIR`
to an existing writable directory records both real fixture CLI entries and
their credential API calls, including the account-free child's separate mount
root. Each database has adjacent source hashes for its actual namespace paths.
This diagnostic measurement preserves the original isolation flags and kernel
refusals; it does not replace a run without measurement or the separate guest
qualification above.

The existing Windows security-floor job and macOS shard zero also publish
source-bound portability observations after their measured tests succeed.
Artifact names include the exact commit, run ID, attempt and platform. Each
contains raw branch data, collection configuration, source hashes and a manifest
with the actual OS, Python and Coverage versions. Sources must match committed
Git blobs before and after collection. Windows checkout preserves those bytes
without line-ending conversion. Consumers must verify the successful producer
job and exact invocation before mapping source-identical filenames. These
observations do not certify native isolation or whole-scope coverage.

### Changes to `core/*`

The `core/` modules sit on the request, authentication, and durability hot paths,
so a change there carries extra weight. State in the commit or PR which invariant
the change preserves — no overlapping claims, no ACL bypass, no unauthenticated
mutation, replay idempotency, no lost durability — and add the test that pins it.
When a hot-path module (`hub.py`, `state.py`, `message_auth.py`, `persistence.py`)
grows a second responsibility, prefer extracting a new module over widening it.

### Structure budget

A long module is not a defect while it holds one responsibility. What points at
a second one is counted from the syntax tree for `src/synapse_channel` and
`tools`: constructor parameters, attributes set in `__init__`, objects a
constructor builds, function length and branches, internal imports of a module,
and access to another object's private members. Units above a threshold are
listed with their exact figure in `tools/structure_budget.toml`.

Known initialisation attributes are also counted through calls to methods of the same
class, starting at `__init__`. The transitive union includes conditional helpers,
recursive calls, assignment forms and literal attribute setters. Moving an
assignment into a helper therefore does not reduce that figure. The original
direct-constructor count remains a separate historical indicator. Calls whose
effects cannot be resolved from the class source are printed as
`initialisation unresolved`; they require source review and do not establish
that the call assigns no attributes. The numeric figure is a known lower bound
when such flags are present; source review must account for those effects.

Schema 2 introduces this indicator without changing the original measures.
When comparing with a schema-1 baseline, its initialisation figures come from
the exact parent commit's Python blobs, measured with the current algorithm.
They never come from the candidate working tree. A listed figure cannot grow
during the migration, and the schema cannot subsequently be downgraded.

The syntactic gate does not establish that a module has one responsibility.
Review growth at the change boundary: name the component that owns the behaviour
and check whether the module, function or class still does one thing. The
300-line module, 50-line function and 10-method class guidelines trigger that
review. A compatibility facade may retain existing entry points while the
owning component holds their implementation; new behaviour belongs with its
owner. Splitting a file or adding forwarding methods alone does not demonstrate
that responsibility has been separated.

```bash
python -m tools.check_structure_budget --check --baseline HEAD    # pre-commit and CI run this
python -m tools.check_structure_budget --update --baseline HEAD   # after shortening a listed unit
python -m tools.check_structure_budget --report --baseline HEAD   # before and after figures
```

A listed figure may only fall, and the ledger is lowered in the same commit. A
new unit above a threshold is refused: split it by responsibility. Moving a
listed unit unchanged is recorded as a `[[moves]]` entry for that one commit. An
`[[exceptions]]` entry with a reason, an owner and a review date is the only way
to accept a unit above a threshold outside the ledger; it fails the check once
its date has passed. Configuration exceptions also expire after their accepted
values enter the baseline, and a threshold cannot exceed its recorded allowance.
A measured root may be added but cannot be removed from the baseline's scope;
otherwise unmeasured source growth could bypass the guard. Both `--check` and
`--update` refuse these changes before the ledger is written.

## Pull requests

1. Branch from `main`.
2. Make the change with tests and docs in the same commit sequence.
3. Run the scoped lint, type and behavioural checks for the change and require
   the applicable hosted CI gates. Reserve an exhaustive local preflight only
   when the task explicitly has resources for it.
4. Open a pull request describing the change, the rationale, and how it was
   verified. Link any related issue.

The wire protocol and coordination model are described in
[`ARCHITECTURE.md`](ARCHITECTURE.md) and [`TEAM_PROTOCOL.md`](TEAM_PROTOCOL.md);
read those before changing message types or the hub state machine.

## Releasing

Cutting a release is owner-gated and runs the mandatory release gate:

1. **Bump** the version across every carrier — `pyproject.toml`,
   `src/synapse_channel/__init__.py`, `CITATION.cff`, `.zenodo.json`,
   `server.json`, and the `README.md` citation — until
   `python tools/check_version_sync.py` reports a single in-sync version;
   regenerate the capability snapshot and open the `CHANGELOG.md` `[X.Y.Z]`
   section. Land the bump on a CI-green base.
2. **Gate.** Run the seven-phase release certification (code hygiene, security
   `0/0/0`, metadata and version-consistency, git hygiene, build plus clean-env
   install smoke, release mechanics, post-tag plan). For this public repository
   the heavy phases are the push-then-CI matrix at the exact head SHA, and the GO
   report cites CI-green there. Any blocker is a NO-GO.
   For 0.99.27, also install the released 0.99.26 wheel into an isolated Python
   environment and run the manual pre-tag rollback check:
   `python tools/verify_delivery_rollback.py /path/to/old-venv/bin/python`.
   Record its output in the private release dossier. It proves that the old hub
   leaves queued v3 work untouched and the new hub can resume its deadline sweep;
   it does not claim that the old hub drains v3 work.
3. **Tag.** Only after the GO report, a distinct-terminal second eye, and the
   owner's explicit publish-go, cut an **annotated** tag at the exact release
   commit: `git tag -a vX.Y.Z <commit>`. The tag is tag-triggered — pushing it
   runs the OIDC trusted-publish to PyPI, which is **irreversible**.
4. **Verify** post-tag: the PyPI version is live with `yanked=false`, the
   published wheel and sdist SHA-256 digests match the build provenance (the
   release workflow prints them in the GitHub Release notes), and the tag
   dereferences to the exact release commit.

### Signed tags (recommended)

Signing a release tag lets a consumer verify it came from a maintainer. The
lowest-burden option is **SSH signing** with the key you already push with:

```
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
```

Register that public key on GitHub as a **Signing Key** (Settings → SSH and GPG
keys → New SSH key → key type *Signing Key*), then cut the tag with `-s`:

```
git tag -s vX.Y.Z <commit>
git verify-tag vX.Y.Z
```

Once maintainers sign tags, `.github/workflows/release.yml` can enforce it by
running `git verify-tag "$tag"` in its tag-validation step before any asset is
published. That local (and CI) `git verify-tag` additionally needs an *allowed
signers* file mapping the signer to its key: GitHub's server-side "Verified"
badge works from the registered Signing Key alone, but the local check does not,
and without it `git verify-tag` fails with `no principal matched`. Configure it
before wiring the CI enforcement:

```
git config --global gpg.ssh.allowedSignersFile ~/.config/git/allowed_signers
# each allowed_signers line: <signer-email-or-principal> <key-type> <public-key>
```
