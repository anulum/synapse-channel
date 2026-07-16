# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one-command launcher for a local hub + worker team
"""Turnkey launcher for a local Synapse team backed by Ollama.

This module starts a hub and one or two model workers as child processes and
prints the command a human runs to join the channel. When Ollama offers no
usable model — absent, unreachable, or empty — the team falls back to a single
offline ``rule`` worker (deterministic canned replies) with a loud caveat,
instead of spawning Ollama workers whose every reply would fail with
"connection refused". The orchestration is split into pure planning helpers
(model detection and command construction) and a thin :func:`run_team` runner
whose process spawning, sleeping, and model detection are all injectable, so the
whole module is unit-testable without a real server.
"""

from __future__ import annotations

import json
import socket
import subprocess  # nosec B404 - fixed provider argv, never a shell string
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

from synapse_channel.core.http_response import read_bounded
from synapse_channel.terminal_text import shell_long_option

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
FAST_MODEL_PREFERENCES = ["gemma3:4b", "gemma3:1b", "llama3", "gemma"]
REASON_MODEL_PREFERENCES = ["gemma3:12b", "gemma4", "llama3", "gemma3:4b"]
FALLBACK_MODEL = "llama3"
SHUTDOWN_TIMEOUT_SECONDS = 2.0
"""Seconds to wait for a child process to exit after graceful termination."""

HUB_READY_TIMEOUT_SECONDS = 5.0
"""Seconds to wait for the hub to accept a connection before declaring it dead."""

_HUB_READY_INTERVAL_SECONDS = 0.2

# A planned child process: a human-readable label and the argv to spawn.
ProcessSpec = tuple[str, list[str]]

ModelDetector = Callable[[list[str]], "str | None"]


def detect_model(
    preferred: list[str],
    *,
    base_url: str = OLLAMA_BASE_URL,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> str | None:
    """Pick a locally available Ollama model matching a preference list.

    Parameters
    ----------
    preferred : list[str]
        Model names or prefixes in priority order.
    base_url : str, optional
        Base URL of the Ollama API. Defaults to :data:`OLLAMA_BASE_URL`.
    opener : Callable, optional
        ``urlopen``-compatible callable, injectable for testing.

    Returns
    -------
    str or None
        The best matching installed model (the bare family name when the
        preference has no tag), the first installed model as a fallback, or
        ``None`` if none are installed or the query fails.
    """
    try:
        with opener(f"{base_url}/api/tags", timeout=2) as response:
            data = json.loads(read_bounded(response, purpose="ollama tags"))
        names: list[str] = [str(model["name"]) for model in data.get("models", [])]
    except Exception:
        return None

    for preference in preferred:
        for name in names:
            if name.startswith(preference) or preference in name:
                return name.split(":")[0] if ":" not in preference else name
    return names[0].split(":")[0] if names else None


def build_hub_command(port: int) -> list[str]:
    """Return the argv that starts a hub on ``port``."""
    return [sys.executable, "-m", "synapse_channel.cli", "hub", "--port", str(port)]


def build_worker_command(name: str, model: str, uri: str, *, provider: str = "ollama") -> list[str]:
    """Return the argv that starts a worker named ``name``.

    The default ``ollama`` provider pins ``model``; the offline ``rule`` provider
    needs no model, so ``--model`` is omitted for it rather than passing a name
    the provider silently ignores.
    """
    argv = [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "worker",
        "--name",
        name,
        "--uri",
        uri,
        "--provider",
        provider,
    ]
    if provider != "rule":
        argv += ["--model", model]
    return argv


def _is_offline_team(specs: list[ProcessSpec]) -> bool:
    """Return whether the plan fell back to an offline ``rule`` worker."""
    for _label, argv in specs:
        if "--provider" in argv:
            index = argv.index("--provider")
            if index + 1 < len(argv) and argv[index + 1] == "rule":
                return True
    return False


def plan_team(
    port: int,
    *,
    no_workers: bool = False,
    fast_model: str | None = None,
    reason_model: str | None = None,
    prefix: str = "",
    detect: ModelDetector = detect_model,
) -> list[ProcessSpec]:
    """Plan the child processes for a team without spawning anything.

    Parameters
    ----------
    port : int
        Hub port; the worker URI is derived from it.
    no_workers : bool, optional
        When ``True`` only the hub is planned. Defaults to ``False``.
    fast_model, reason_model : str or None, optional
        Explicit model overrides; when ``None`` they are auto-detected.
    prefix : str, optional
        Namespace prepended to every worker name, so a team can run per project
        without clashing with another project's roster. Defaults to ``""``.
    detect : ModelDetector, optional
        Model-detection callable, injectable for testing.

    Returns
    -------
    list[ProcessSpec]
        The hub spec followed by zero, one, or two worker specs. A second
        worker is added only when the reasoning model differs from the fast one.
    """
    uri = f"ws://localhost:{port}"
    specs: list[ProcessSpec] = [("hub", build_hub_command(port))]
    if no_workers:
        return specs

    fast = fast_model or detect(FAST_MODEL_PREFERENCES)
    if fast is None and reason_model is None:
        # No operator model hint and Ollama offers nothing usable: start one
        # offline rule worker so the team still answers deterministically. An
        # explicit --model (either role) keeps the Ollama provider instead.
        fast_name = f"{prefix}FAST"
        specs.append(
            (fast_name, build_worker_command(fast_name, FALLBACK_MODEL, uri, provider="rule"))
        )
        return specs

    fast = fast or FALLBACK_MODEL
    reason = reason_model or detect(REASON_MODEL_PREFERENCES) or fast
    fast_name = f"{prefix}FAST"
    specs.append((fast_name, build_worker_command(fast_name, fast, uri)))
    if reason != fast:
        reason_name = f"{prefix}REASON"
        specs.append((reason_name, build_worker_command(reason_name, reason, uri)))
    return specs


def _print_instructions(port: int, prefix: str = "") -> None:
    """Print how to join the running team from another terminal."""
    uri = f"ws://localhost:{port}"
    print("\n--- READY ---")
    print("Join the channel from another pane/window:")
    print(
        "    synapse listen "
        f"{shell_long_option('--uri', uri)} {shell_long_option('--name', 'USER')}"
    )
    print("Send a message from the command line:")
    print(
        "    synapse send "
        f"{shell_long_option('--uri', uri)} "
        f'{shell_long_option("--target", f"{prefix}FAST")} -- "status?"'
    )
    print("Workers reply when mentioned or when USER addresses the room.")
    print("Ctrl+C here stops the background workers + hub.\n")


def _shutdown(
    procs: list[tuple[str, subprocess.Popen[str]]],
    *,
    timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> None:
    """Terminate every still-running child process, killing if needed."""
    for _label, proc in reversed(procs):
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout_seconds)
            except Exception:
                proc.kill()
                proc.wait(timeout=timeout_seconds)


def _hub_is_listening(port: int, *, connect: Callable[..., Any] = socket.create_connection) -> bool:
    """Return whether a TCP connection to the local hub port succeeds."""
    try:
        with connect(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _await_hub_ready(
    port: int,
    *,
    sleep: Callable[[float], None],
    is_listening: Callable[[int], bool],
    timeout_seconds: float = HUB_READY_TIMEOUT_SECONDS,
    interval: float = _HUB_READY_INTERVAL_SECONDS,
) -> bool:
    """Poll the hub port until it accepts a connection or the deadline passes."""
    attempts = max(1, int(timeout_seconds / interval))
    for _ in range(attempts):
        if is_listening(port):
            return True
        sleep(interval)
    return False


def run_team(
    port: int = 8876,
    *,
    no_workers: bool = False,
    fast_model: str | None = None,
    reason_model: str | None = None,
    prefix: str = "",
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    detect: ModelDetector = detect_model,
    is_hub_ready: Callable[[int], bool] = _hub_is_listening,
    shutdown_timeout_seconds: float = SHUTDOWN_TIMEOUT_SECONDS,
) -> int:
    """Spawn a hub and workers, then monitor them until one exits.

    Parameters
    ----------
    port : int, optional
        Hub port. Defaults to ``8876``.
    no_workers : bool, optional
        When ``True`` only the hub is started. Defaults to ``False``.
    fast_model, reason_model : str or None, optional
        Explicit model overrides; auto-detected when ``None``.
    prefix : str, optional
        Namespace prepended to every worker name. Defaults to ``""``.
    popen : Callable, optional
        ``subprocess.Popen``-compatible spawner, injectable for testing.
    sleep : Callable, optional
        ``time.sleep``-compatible delay, injectable for testing.
    detect : ModelDetector, optional
        Model-detection callable, injectable for testing.
    is_hub_ready : Callable, optional
        Predicate that reports whether the hub is accepting connections on the
        port, injectable for testing. Defaults to a real TCP probe.
    shutdown_timeout_seconds : float, optional
        Seconds to wait during shutdown before killing a child process.

    Returns
    -------
    int
        ``0`` on a clean ``Ctrl+C`` shutdown, ``1`` if the hub never started
        listening, otherwise the exit code of the first child that terminated
        (or ``1`` when it exited without a code).
    """
    specs = plan_team(
        port,
        no_workers=no_workers,
        fast_model=fast_model,
        reason_model=reason_model,
        prefix=prefix,
        detect=detect,
    )
    procs: list[tuple[str, subprocess.Popen[str]]] = []
    print(f"=== SYNAPSE CHANNEL — TEAM LAUNCH (ws://localhost:{port}) ===")
    if _is_offline_team(specs):
        print(
            f"[team] No Ollama model detected at {OLLAMA_BASE_URL} — starting one OFFLINE "
            "rule-based worker (deterministic canned replies). Start Ollama and re-run for "
            "real model replies."
        )
    try:
        for label, argv in specs:
            proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            procs.append((label, proc))
            print(f"[launch] {label} started (pid={proc.pid})")
            if label == "hub" and not _await_hub_ready(
                port, sleep=sleep, is_listening=is_hub_ready
            ):
                print(
                    f"[launch] hub failed to start listening on port {port}; aborting.",
                    file=sys.stderr,
                )
                return 1

        _print_instructions(port, prefix)

        while True:
            sleep(1.5)
            for label, proc in procs:
                code = proc.poll()
                if code is not None:
                    print(f"[launch] {label} exited (code {code})")
                    return code or 1
    except KeyboardInterrupt:
        print("\n[launch] Shutting down...")
        return 0
    finally:
        _shutdown(procs, timeout_seconds=shutdown_timeout_seconds)
