# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — one-command launcher for a local hub + worker team
"""Turnkey launcher for a local Synapse team backed by Ollama.

This module starts a hub and one or two model workers as child processes and
prints the command a human runs to join the channel. The orchestration is split
into pure planning helpers (model detection and command construction) and a thin
:func:`run_team` runner whose process spawning, sleeping, and model detection are
all injectable, so the whole module is unit-testable without a real server.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
FAST_MODEL_PREFERENCES = ["gemma3:4b", "gemma3:1b", "llama3", "gemma"]
REASON_MODEL_PREFERENCES = ["gemma3:12b", "gemma4", "llama3", "gemma3:4b"]
FALLBACK_MODEL = "llama3"

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
            data = json.loads(response.read())
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


def build_worker_command(name: str, model: str, uri: str) -> list[str]:
    """Return the argv that starts an Ollama-backed worker named ``name``."""
    return [
        sys.executable,
        "-m",
        "synapse_channel.cli",
        "worker",
        "--name",
        name,
        "--uri",
        uri,
        "--provider",
        "ollama",
        "--model",
        model,
    ]


def plan_team(
    port: int,
    *,
    no_workers: bool = False,
    fast_model: str | None = None,
    reason_model: str | None = None,
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

    fast = fast_model or detect(FAST_MODEL_PREFERENCES) or FALLBACK_MODEL
    reason = reason_model or detect(REASON_MODEL_PREFERENCES) or fast
    specs.append(("FAST", build_worker_command("FAST", fast, uri)))
    if reason != fast:
        specs.append(("REASON", build_worker_command("REASON", reason, uri)))
    return specs


def _print_instructions(port: int) -> None:
    """Print how to join the running team from another terminal."""
    uri = f"ws://localhost:{port}"
    print("\n--- READY ---")
    print("Join the channel from another pane/window:")
    print(f"    synapse listen --uri {uri} --name USER")
    print("Send a message from the command line:")
    print(f'    synapse send --uri {uri} --name USER --target FAST "status?"')
    print("Workers reply when mentioned or when USER addresses the room.")
    print("Ctrl+C here stops the background workers + hub.\n")


def _shutdown(procs: list[tuple[str, subprocess.Popen[str]]]) -> None:
    """Terminate every still-running child process, killing if needed."""
    for _label, proc in reversed(procs):
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


def run_team(
    port: int = 8876,
    *,
    no_workers: bool = False,
    fast_model: str | None = None,
    reason_model: str | None = None,
    popen: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    detect: ModelDetector = detect_model,
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
    popen : Callable, optional
        ``subprocess.Popen``-compatible spawner, injectable for testing.
    sleep : Callable, optional
        ``time.sleep``-compatible delay, injectable for testing.
    detect : ModelDetector, optional
        Model-detection callable, injectable for testing.

    Returns
    -------
    int
        ``0`` on a clean ``Ctrl+C`` shutdown, otherwise the exit code of the
        first child that terminated (or ``1`` when it exited without a code).
    """
    specs = plan_team(
        port,
        no_workers=no_workers,
        fast_model=fast_model,
        reason_model=reason_model,
        detect=detect,
    )
    procs: list[tuple[str, subprocess.Popen[str]]] = []
    print(f"=== SYNAPSE CHANNEL — TEAM LAUNCH (ws://localhost:{port}) ===")
    try:
        for label, argv in specs:
            proc = popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            procs.append((label, proc))
            print(f"[launch] {label} started (pid={proc.pid})")
            if label == "hub":
                sleep(0.7)

        _print_instructions(port)

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
        _shutdown(procs)
