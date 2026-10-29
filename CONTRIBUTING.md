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

The Linux matrix runs two indexed pytest shards for each supported interpreter.
Pytest first collects its full native inventory, sorts node IDs and assigns
alternating IDs to the two runners. Every runner keeps the same native backends
and interpreter-specific host prerequisites. Shard zero also executes the
explicit Linux metadata-refusal follow-up; each interpreter runs it once.

`tools.pytest_shard` records the complete inventory, selected IDs and actual
completed outcomes, bound to the source SHA, run, interpreter and configuration.
`tools/ci_shard_artifacts.py pack` refuses unsuccessful or incomplete receipts.
Only successful shards publish authoritative indexed artifacts. `test-complete`
requires every shard, verifies identical interpreter inventories and an exact
union without omissions or duplicates, combines actual Python 3.12 branch and
subprocess coverage, then applies the original whole-package 98% threshold.
A shard has no individual coverage floor because it measures only its partition.
Missing, failed, skipped, duplicate, foreign or altered artifacts fail the gate.
The required `ci` job includes both the matrix and the complete-union gate.

Focused runner/artifact checks live in `tests/test_ci_shard_artifacts.py` and use
real pytest and coverage subprocesses. Hosted timing comparisons must include
setup and queue time; sharding alone is not evidence of measured improvement.

## Standards a change must meet

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
guard's rejection behavior, while the explicit profile proves Linux behavior.

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

- **Tests.** Every new module, function, and branch ships with tests. Aggregate
  coverage stays above the 98% floor; a change must not lower it.
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
3. Run the scoped lint, type and behavioral checks for the change and require
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
