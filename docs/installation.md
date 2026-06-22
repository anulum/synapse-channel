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

## Verifying

```bash
synapse --version
```

## Staying up to date

`synapse --version` checks PyPI at most once a day and appends a one-line notice
when a newer release is available:

```text
synapse-channel 0.31.0
  → 0.32.0 is available (you have 0.31.0): pipx upgrade synapse-channel
    (set SYNAPSE_NO_UPDATE_CHECK=1 to silence)
```

The check is best-effort: it never blocks the command, is silent when offline, and
is disabled entirely by `SYNAPSE_NO_UPDATE_CHECK=1`. Each release is also published
on the [GitHub releases](https://github.com/anulum/synapse-channel/releases) page
with notes from the changelog — watch the repository (**Watch → Custom → Releases**)
to be notified of every update.
