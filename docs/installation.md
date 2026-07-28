# Installation

SYNAPSE CHANNEL requires Python 3.10 or newer.

## From PyPI

```bash
pip install synapse-channel
```

For the `synapse` command on your `PATH` as an isolated CLI, use
[pipx](https://pipx.pypa.io/):

```bash
pipx install synapse-channel
```

## From source

```bash
git clone https://github.com/anulum/synapse-channel
cd synapse-channel
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

This installs the `synapse` console command and the `synapse_channel` package.

## Published-wheel integrity

The release workflow installs the exact built wheel in a clean environment
before publication. It compares the installed console-script metadata with
`pyproject.toml`, requires every generated wrapper, loads every declared
callable, and refuses any `synapse_channel` module that resolves outside the
clean environment's site-packages tree. This covers all 13 commands, including
the short `syn`, `syn-*`, `synapse`, and `synapse-channel` entry points; checking
only `synapse --help` would not detect an omitted alias module.

## Optional extras

| Extra | Adds |
| --- | --- |
| `dev` | The development toolchain (ruff, mypy, pytest, pre-commit). |
| `benchmark` | `tiktoken`, for real token counts in the relay benchmark. |
| `docs` | The documentation-site toolchain (MkDocs Material, mkdocstrings). |
| `otel` | The OpenTelemetry SDK + OTLP/HTTP exporters, for `synapse causality otel --endpoint` and `synapse fleet-scorecard --endpoint`. |
| `semantic` | Local tree-sitter runtime and Python, JavaScript/JSX, TypeScript/TSX, Rust, and Go grammar wheels for function-level Git-diff claims. |

Install one or more with, for example:

```bash
pip install -e ".[dev,benchmark]"
```

For offline-capable semantic diff inference after installation:

```bash
pip install 'synapse-channel[semantic]'
python tools/semantic_diff_claims.py --base main --check
```

The grammar wheels are installed up front. Claim resolution never downloads a
parser at runtime.

For a contributor checkout, the local `.venv` should mirror the declared
development, documentation, and benchmark extras. Verify that before running
larger local gates:

```bash
.venv/bin/python tools/check_dev_dependency_drift.py --check
.venv/bin/python tools/audit_dependency_tooling.py --check
```

`audit_dependency_tooling.py` is an offline maintenance audit. It checks that
the local preflight script still includes ruff, mypy, pytest, Bandit, MkDocs,
pip-audit, dependency drift, and this audit; that workflow actions are pinned to
full commit SHAs; that Dependabot watches GitHub Actions, Python, and Docker;
and that PyPI publish/download tracking surfaces remain wired.

## Verifying

```bash
synapse --version
synapse doctor
```

## Fastest safe trial path

Use one self-contained path before changing a real checkout:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo --output ./synapse-golden-demo
```

The demo starts and stops its own local hub, uses a disposable committed Git
repository, proves separate claims and overlapping-claim refusal, denies a
mutation before handoff, permits it after handoff, and writes an observed
verification receipt plus a static dashboard. It needs no persistent hub,
provider CLI, Git hook, MCP host, or A2A bridge. The same exact three-command
block is regression-bound across the README, quick start, CLI reference, and
this installation guide, and its `synapse demo` command is exercised as a real
subprocess.

After that proof passes, use `synapse fleet-init --fix` to prepare a persistent
local workspace, hub, and waiter, then run `synapse git-init --name
trial-agent` inside the real repository before an agent edits it. Optional A2A
interoperability is a follow-on in the [A2A bridge guide](a2a-conformance.md); it is not a
prerequisite for first coordination value.

## Staying up to date

`synapse --version` is network-silent by default. If you want it to check PyPI
for newer releases, opt in explicitly:

```bash
SYNAPSE_UPDATE_CHECK=1 synapse --version
```

The opt-in check queries PyPI at most once a day and appends a one-line notice
when a newer release is available:

```text
synapse-channel 0.31.0
  → 0.32.0 is available (you have 0.31.0): pipx upgrade synapse-channel
    (unset SYNAPSE_UPDATE_CHECK or set SYNAPSE_NO_UPDATE_CHECK=1 to silence)
```

The check is best-effort: it never blocks the command, is silent when offline, and
is disabled unless `SYNAPSE_UPDATE_CHECK=1` is present. Each release is also
published on the [GitHub releases](https://github.com/anulum/synapse-channel/releases)
page with notes from the changelog — watch the repository (**Watch → Custom →
Releases**) to be notified of every update.
