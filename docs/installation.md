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

## Optional extras

| Extra | Adds |
| --- | --- |
| `dev` | The development toolchain (ruff, mypy, pytest, pre-commit). |
| `benchmark` | `tiktoken`, for real token counts in the relay benchmark. |
| `docs` | The documentation-site toolchain (MkDocs Material, mkdocstrings). |

Install one or more with, for example:

```bash
pip install -e ".[dev,benchmark]"
```

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

After installation, validate the CLI and then opt into repo wiring deliberately:

```bash
python -m pip install synapse-channel
synapse doctor
synapse demo
synapse quickstart-coding
synapse git-init --name trial-agent
synapse a2a-card --endpoint-url http://127.0.0.1:8877
synapse a2a-serve --endpoint-url http://127.0.0.1:8877
```

Run this in a disposable or already-versioned repository. The A2A bridge command
above binds to localhost; add bearer auth before any non-loopback exposure.

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
