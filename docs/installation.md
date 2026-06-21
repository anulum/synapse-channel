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
