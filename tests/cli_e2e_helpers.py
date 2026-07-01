# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
"""End-to-end helpers that drive the packaged CLI against an isolated hub.

The unit suite exercises handlers in-process; these helpers instead run the real
``synapse`` entrypoint as a subprocess against a throwaway hub bound to a free
port with a temporary database, so a command is tested exactly as a user invokes
it — argument parsing, process exit code, and printed output included. The hub is
never the shared workstation hub on port 8876; every journey gets its own.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_CLI = [sys.executable, "-m", "synapse_channel.cli"]


def free_port() -> int:
    """Return a currently-free localhost TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def git_run(repo: Path, *args: str) -> None:
    """Run a git command inside ``repo``, raising on failure."""
    subprocess.run(  # noqa: S603, S607 - fixed git args, test-only
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )


def git_repo(root: Path) -> Path:
    """Create a minimal committed git repository at ``root`` and return it."""
    root.mkdir(parents=True, exist_ok=True)
    git_run(root, "init", "-q")
    git_run(root, "config", "user.email", "e2e@example.test")
    git_run(root, "config", "user.name", "e2e")
    git_run(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("e2e\n", encoding="utf-8")
    git_run(root, "add", "-A")
    git_run(root, "commit", "-q", "-m", "seed")
    return root


def _await_listening(port: int, timeout: float = 8.0) -> None:
    """Block until ``port`` accepts a connection, or raise ``TimeoutError``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"hub did not start listening on {port}")


@dataclass(frozen=True)
class CliResult:
    """The captured outcome of one CLI subprocess invocation."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Return stdout and stderr joined, for lenient substring assertions."""
        return f"{self.stdout}{self.stderr}"

    def ok(self) -> bool:
        """Return whether the process exited zero."""
        return self.returncode == 0


def run_cli(
    *args: str,
    uri: str | None = None,
    timeout: float = 20.0,
    stdin: str | None = None,
    cwd: Path | None = None,
) -> CliResult:
    """Run ``synapse <args>`` as a subprocess and capture its result.

    Parameters
    ----------
    args : str
        CLI arguments after ``synapse`` (e.g. ``"who"``).
    uri : str or None, optional
        When set, append ``--uri <uri>`` so the command targets an isolated hub
        rather than the default ``ws://localhost:8876``.
    timeout : float, optional
        Seconds before the subprocess is killed and the call fails.
    stdin : str or None, optional
        Optional text piped to the process stdin.
    cwd : pathlib.Path or None, optional
        Working directory for the process; used by the git-aware commands that
        read the current repository.
    """
    argv = [*args]
    if uri is not None:
        # Insert before any ``--`` separator: for commands like ``lock <task> --
        # <cmd>`` everything after ``--`` is the held command, so a trailing
        # ``--uri`` would bind the wrong (default) hub — a silent cross-hub leak.
        if "--" in argv:
            cut = argv.index("--")
            argv = [*argv[:cut], "--uri", uri, *argv[cut:]]
        else:
            argv += ["--uri", uri]
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, test-only
        [*_CLI, *argv],
        capture_output=True,
        text=True,
        timeout=timeout,
        input=stdin,
        cwd=None if cwd is None else str(cwd),
        check=False,
    )
    return CliResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@dataclass(frozen=True)
class IsolatedHub:
    """A running throwaway hub: its ``ws://`` URI, port, and database path."""

    uri: str
    port: int
    db_path: Path


@contextmanager
def isolated_hub(
    tmp_path: Path,
    *,
    extra_args: Sequence[str] = (),
    ready_timeout: float = 8.0,
) -> Iterator[IsolatedHub]:
    """Start ``synapse hub`` on a free port with a temp DB; stop it on exit.

    Yields an :class:`IsolatedHub`. The hub is durable (``--db``) so replay,
    reproduce, merkle, and causality journeys can read the same event log the
    coordination journey wrote.
    """
    port = free_port()
    db_path = tmp_path / "e2e-hub.db"
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [*_CLI, "hub", "--port", str(port), "--db", str(db_path), *extra_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _await_listening(port, timeout=ready_timeout)
        yield IsolatedHub(uri=f"ws://localhost:{port}", port=port, db_path=db_path)
    finally:
        _stop(proc)


def _stop(proc: subprocess.Popen[str]) -> None:
    """Terminate a subprocess, escalating to kill if it does not stop."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def http_get(url: str, timeout: float = 5.0) -> tuple[int, str]:
    """GET ``url`` and return ``(status, body)``; status 0 means unreachable."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed http scheme
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8")
    except (urllib.error.URLError, OSError):
        return 0, ""


@contextmanager
def isolated_dashboard(hub_uri: str, *, ready_timeout: float = 8.0) -> Iterator[str]:
    """Serve ``synapse dashboard`` against ``hub_uri``; yield its base HTTP URL.

    Blocks until ``/snapshot.json`` answers, so the caller can fetch the read-only
    fleet snapshot the cockpit and other clients consume.
    """
    port = free_port()
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [*_CLI, "dashboard", "--port", str(port), "--host", "127.0.0.1", "--uri", hub_uri],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            status, _ = http_get(f"{base}/snapshot.json", timeout=1.0)
            if status == 200:
                break
            time.sleep(0.1)
        yield base
    finally:
        _stop(proc)
