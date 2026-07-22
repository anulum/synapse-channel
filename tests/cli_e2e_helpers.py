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

import contextlib
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

# ``-u`` forces the child interpreter's stdout/stderr unbuffered. The server
# helpers capture a subprocess's merged output and, on a readiness timeout, kill
# it with SIGTERM before reading that pipe. A block-buffered startup banner would
# be lost on the kill, so a hung server reports an empty capture that cannot
# distinguish "printed a banner we never flushed" from "blocked before any print".
# Unbuffered output makes the captured diagnostic trustworthy (see the macOS e2e
# server-hang investigation); it changes buffering only, never behaviour.
_CLI = [sys.executable, "-u", "-m", "synapse_channel.cli"]
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CANDIDATE_PYTHONPATH = (
    (_PROJECT_ROOT / "src").as_posix(),
    (_PROJECT_ROOT / "tests").as_posix(),
)


def _candidate_environment(overrides: Mapping[str, str] | None) -> dict[str, str]:
    """Return a child environment that imports this candidate checkout first.

    CLI journeys often execute from a temporary Git repository. Relative
    ``PYTHONPATH`` entries such as ``src`` would then resolve inside that
    repository and silently fall back to an installed package instead of the
    candidate under test.
    """
    environment = dict(os.environ)
    if overrides is not None:
        environment.update(overrides)
    inherited = tuple(
        entry for entry in environment.get("PYTHONPATH", "").split(os.pathsep) if entry
    )
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys((*_CANDIDATE_PYTHONPATH, *inherited)))
    return environment


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
    env: Mapping[str, str] | None = None,
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
    env : Mapping[str, str] or None, optional
        Extra environment variables layered over the inherited environment; used
        to exercise ``SYNAPSE_URI`` hub selection without passing ``--uri``.
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
        env=_candidate_environment(env),
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
    coordination journey wrote. Its trust-on-first-use pin store is temporary
    too: signed test clients must never read or mutate the developer's
    ``~/synapse/identity-pins.json`` across pytest sessions.
    """
    port = free_port()
    db_path = tmp_path / "e2e-hub.db"
    identity_pins = tmp_path / "e2e-identity-pins.json"
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [
            *_CLI,
            "hub",
            "--port",
            str(port),
            "--db",
            str(db_path),
            "--identity-pins",
            str(identity_pins),
            *extra_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _await_listening(port, timeout=ready_timeout)
        # Hand subprocess clients an explicit loopback address, not "localhost".
        # macOS resolves "localhost" to ::1 (IPv6) first, and a client that connects
        # to a hub bound on 127.0.0.1 can hang there; e2e should exercise the
        # dashboard/A2A behaviour, not host-name resolution. The hub's own
        # "localhost" default robustness on macOS is tracked separately.
        yield IsolatedHub(uri=f"ws://127.0.0.1:{port}", port=port, db_path=db_path)
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


def _stop_group(proc: subprocess.Popen[str]) -> None:
    """Terminate a whole process group, for launchers that fork child processes.

    ``synapse team`` starts its hub (and any workers) as children in the session
    it leads, so signalling only the parent would orphan them; this signals the
    group so the hub's port is released before the next journey binds one.
    """
    try:
        group = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(group, sig)
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def http_get(
    url: str,
    timeout: float = 5.0,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """GET ``url`` and return ``(status, body)``; status 0 means unreachable."""
    try:
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            return int(response.status), response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return int(error.code), error.read().decode("utf-8")
    except (urllib.error.URLError, OSError):
        return 0, ""


A2A_AGENT_CARD_PATH = "/.well-known/agent-card.json"
"""Well-known path the A2A bridge serves its Agent Card on."""


@contextmanager
def isolated_a2a_serve(
    hub_uri: str,
    *,
    allowed_origins: tuple[str, ...] = (),
    ready_timeout: float = 8.0,
) -> Iterator[str]:
    """Serve ``synapse a2a-serve`` against ``hub_uri``; yield its base HTTP URL.

    Blocks until the Agent Card endpoint answers, so the caller can fetch the card
    the bridge projects from the hub's live capability manifest. Bound to loopback
    only; no bearer auth is set because it never leaves the test host.
    """
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    origin_args = [part for origin in allowed_origins for part in ("--allow-origin", origin)]
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [
            *_CLI,
            "a2a-serve",
            "--uri",
            hub_uri,
            "--name",
            "BRIDGE",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--endpoint-url",
            base,
            *origin_args,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _await_http_ready(proc, f"{base}{A2A_AGENT_CARD_PATH}", ready_timeout=ready_timeout)
        yield base
    finally:
        _stop(proc)


@contextmanager
def isolated_worker(
    hub_uri: str,
    *,
    name: str = "BOT",
    provider: str = "rule",
    ready_timeout: float = 12.0,
) -> Iterator[str]:
    """Run ``synapse worker`` against ``hub_uri``; yield the worker's identity.

    Blocks until the worker is registered on the hub (it appears in ``who``), so
    a message the caller sends afterwards cannot race the worker's connection. The
    ``rule`` provider is offline and deterministic — it acknowledges without
    reaching any network — so the journey needs no model credentials.
    """
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [*_CLI, "worker", "--provider", provider, "--name", name, "--uri", hub_uri],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            who = run_cli("who", uri=hub_uri, timeout=5.0)
            if name in who.stdout:
                break
            time.sleep(0.1)
        yield name
    finally:
        _stop(proc)


@contextmanager
def isolated_supervisor(
    hub_uri: str,
    *,
    name: str = "SUPERVISOR",
    idle_seconds: float = 1.0,
    interval: float = 0.5,
    ready_timeout: float = 12.0,
) -> Iterator[str]:
    """Run ``synapse supervisor`` against ``hub_uri``; yield its identity.

    Predictive-stall history is disabled and the idle ceiling is tiny so a task
    left in progress is re-offered within a second, keeping the journey fast and
    deterministic. Blocks until the supervisor registers on the hub.
    """
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [
            *_CLI,
            "supervisor",
            "--name",
            name,
            "--idle-seconds",
            str(idle_seconds),
            "--interval",
            str(interval),
            "--no-predictive-stall",
            "--uri",
            hub_uri,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + ready_timeout
        while time.monotonic() < deadline:
            who = run_cli("who", uri=hub_uri, timeout=5.0)
            if name in who.stdout:
                break
            time.sleep(0.1)
        yield name
    finally:
        _stop(proc)


@contextmanager
def isolated_team(*, no_workers: bool = True, ready_timeout: float = 10.0) -> Iterator[str]:
    """Launch ``synapse team`` on a free port; yield the hub URI it stands up.

    ``team`` is a one-command launcher that starts its own hub (and, without
    ``--no-workers``, a roster of workers). The journey uses ``--no-workers`` so
    no model provider is needed — the worker reply path is covered separately —
    and only asserts the launcher stands up a reachable, usable hub. A temporary
    ``HOME`` keeps the child hub's default identity-pin file away from the
    workstation's real pins; subprocess clients still share pytest's isolated
    ``XDG_DATA_HOME`` key. The launcher forks its hub as a child, so teardown
    signals the whole process group.
    """
    port = free_port()
    argv = [*_CLI, "team", "--port", str(port)]
    if no_workers:
        argv.append("--no-workers")
    with tempfile.TemporaryDirectory(prefix="synapse-team-e2e-") as home:
        proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            env={**os.environ, "HOME": home},
        )
        try:
            _await_listening(port, timeout=ready_timeout)
            yield f"ws://localhost:{port}"
        finally:
            _stop_group(proc)


def _await_http_ready(
    proc: subprocess.Popen[str],
    url: str,
    *,
    ready_timeout: float,
    headers: dict[str, str] | None = None,
) -> None:
    """Block until ``url`` answers 200, or raise with the subprocess output.

    The launched server (dashboard, A2A bridge) runs with a merged stdout/stderr
    pipe. When it never becomes ready the caller would otherwise assert on a bare
    ``status=0`` that hides the cause — the failure mode seen on macOS runners. So
    on timeout (or an early exit) stop the process, drain that pipe, and surface it
    in the error, making a bind failure, hub-connect failure, or traceback visible.
    On the ready path this returns before the deadline and behaviour is unchanged.
    """
    deadline = time.monotonic() + ready_timeout
    while time.monotonic() < deadline:
        status, _ = http_get(url, timeout=1.0, headers=headers)
        if status == 200:
            return
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    _stop(proc)
    # ``_stop`` has waited for exit, so the merged pipe now reads buffered output
    # to EOF without blocking.
    output = proc.stdout.read() if proc.stdout is not None else ""
    raise RuntimeError(
        f"server did not answer {url} within {ready_timeout}s "
        f"(exit={proc.returncode}); captured output:\n{output.strip() or '<empty>'}"
    )


@contextmanager
def isolated_dashboard(hub_uri: str, *, ready_timeout: float = 8.0) -> Iterator[tuple[str, str]]:
    """Serve ``synapse dashboard`` against ``hub_uri``; yield ``(base_url, bearer)``.

    Live/page reads are gated by a bearer, so the dashboard is launched with a known
    ``--dashboard-token`` and the caller receives it to authorise its requests. Blocks
    until ``/snapshot.json`` answers, so the caller can fetch the read-only fleet
    snapshot the cockpit and other clients consume.
    """
    port = free_port()
    token = "e2e-dashboard-token"  # nosec B105 - fixed loopback test bearer, not a secret
    proc = subprocess.Popen(  # noqa: S603 - fixed interpreter, test-only
        [
            *_CLI,
            "dashboard",
            "--port",
            str(port),
            "--host",
            "127.0.0.1",
            "--uri",
            hub_uri,
            "--dashboard-token",
            token,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    auth = {"Authorization": f"Bearer {token}"}
    try:
        _await_http_ready(proc, f"{base}/snapshot.json", ready_timeout=ready_timeout, headers=auth)
        yield base, token
    finally:
        _stop(proc)
