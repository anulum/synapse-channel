# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — private native composer acceptance
"""Exercise managed wakes with a real pinned client and deterministic provider."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from fixtures.opencode.llm import ScriptedLlmServer
from fixtures.opencode.process import find_opencode, isolated_environment
from synapse_channel import agent_tmux as tmux_module
from synapse_channel.agent_tmux import AgentTmuxConfig, AgentTmuxWakeResult, start_session
from synapse_channel.opencode_composer_probe import ComposerSnapshot, inspect_editor_composer


@dataclass(frozen=True)
class NativeComposer:
    """One task-owned native pane, isolated filesystem and provider."""

    root: Path
    base: tuple[str, ...]
    config: AgentTmuxConfig
    provider: ScriptedLlmServer
    start_result: AgentTmuxWakeResult | None = None

    def tmux(self, *args: str) -> str:
        """Run a bounded command against this fixture's private tmux server."""
        return subprocess.run(
            [*self.base, *args], capture_output=True, text=True, check=True, timeout=10
        ).stdout

    def screen(self) -> str:
        """Capture only the owned native pane."""
        return self.tmux("capture-pane", "-t", self.config.session, "-p", "-J")

    def wait(self, predicate: Callable[[], bool], *, seconds: float = 30) -> None:
        """Wait finitely for an actual native/provider observation."""
        deadline = time.monotonic() + seconds
        while not predicate():
            dead = self.tmux(
                "display-message",
                "-p",
                "-t",
                self.config.session,
                "#{pane_dead}:#{pane_dead_status}",
            ).strip()
            if dead.startswith("1:"):
                raise AssertionError("native composer exited " + dead + "\n" + self.screen())
            if time.monotonic() >= deadline:
                raise AssertionError("native composer deadline\n" + self.screen())
            time.sleep(0.05)

    def text(self, text: str) -> None:
        """Type literal fixture text without accepting it."""
        self.tmux("send-keys", "-t", self.config.session, "-l", text)

    def wait_requests(self, expected: int) -> None:
        """Observe an exact number of real native provider requests."""
        self.wait(lambda: len(self.provider.prompt_requests) == expected)

    def keys(self, *keys: str) -> None:
        """Send explicit fixture control keys to its own native pane."""
        self.tmux("send-keys", "-t", self.config.session, *keys)

    def paste(self, text: str) -> None:
        """Stage exact UTF-8 text using the native bracketed paste path."""
        self.tmux("set-buffer", "-b", "fixture-draft", "--", text)
        self.tmux("paste-buffer", "-b", "fixture-draft", "-d", "-p", "-t", self.config.session)
        time.sleep(0.1)

    def readback(self) -> ComposerSnapshot:
        """Read exact native input using the production callback."""
        return inspect_editor_composer(self.config, self.screen(), runner=subprocess.run)

    def seed(self) -> None:
        """Load a completed native turn whose transcript contains Thinking."""
        self.provider.enqueue_text("Thinking historical transcript marker. NATIVE_SEED_COMPLETED")
        self.text("Reply NATIVE_SEED_COMPLETED without tools.")
        self.keys("Enter")
        self.wait(
            lambda: (
                len(self.provider.prompt_requests) == 1
                and "NATIVE_SEED_COMPLETED" in self.screen()
                and "Thinking historical transcript" in self.screen()
                and "interrupt" not in self.screen()
            )
        )
        time.sleep(0.2)


def controller_config_code(config: AgentTmuxConfig) -> str:
    """Build the configuration preamble for a task-owned crash-controller child.

    Parameters
    ----------
    config : AgentTmuxConfig
        Native fixture identity, command, paths and private tmux endpoint.

    Returns
    -------
    str
        Imports and an equivalent opted-in configuration named cfg. The caller
        supplies its actual crash boundary and operation after this preamble.
    """
    return (
        "from pathlib import Path\nimport os,signal,subprocess\n"
        "from synapse_channel.agent_tmux import AgentTmuxConfig,inject_wake\n"
        f"cfg=AgentTmuxConfig({config.identity!r},{config.session!r},"
        f"Path({str(config.cwd)!r}),agent_command={config.agent_command!r},"
        f"tmux_bin={config.tmux_bin!r},registry_dir=Path({str(config.registry_dir)!r}),"
        "opencode_editor_readback=True)\n"
    )


def _native_environment(
    home: Path, root: Path, provider: ScriptedLlmServer, keybinds: Mapping[str, str] | None
) -> dict[str, str]:
    """Exclude owner credentials and retain only explicit fixture settings."""
    inherited = isolated_environment(home, provider.url, pure=True, disable_project_config=True)
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "HOME",
        "NO_COLOR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "OPENCODE_TEST_HOME",
        "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_AUTH_CONTENT",
        "OPENCODE_DISABLE_AUTOUPDATE",
        "OPENCODE_DISABLE_AUTOCOMPACT",
        "OPENCODE_DISABLE_MODELS_FETCH",
        "OPENCODE_EXPERIMENTAL_DISABLE_FILEWATCHER",
        "OPENCODE_PURE",
        "OPENCODE_DISABLE_PROJECT_CONFIG",
        "COVERAGE_PROCESS_CONFIG",
        "COVERAGE_PROCESS_START",
        "COVERAGE_FILE",
        "COVERAGE_RCFILE",
        "COVERAGE_CORE",
        "PYTHONDONTWRITEBYTECODE",
        "TMUX_TMPDIR",
    }
    environment = {key: value for key, value in inherited.items() if key in allowed}
    environment.update(
        TERM="xterm-256color",
        TMPDIR=str(root / "tmp"),
        PYTHONPATH=str(Path(tmux_module.__file__).resolve().parent.parent),
    )
    config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])
    config["permission"] = "deny"
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    if keybinds is not None:
        path = root / "tui.json"
        path.write_text(json.dumps({"keybinds": dict(keybinds)}), encoding="utf-8")
        environment["OPENCODE_TUI_CONFIG"] = str(path)
    return environment


def _release_extracted_libraries(root: Path) -> None:
    """Remove only closed regenerated libraries, preserving native records."""
    libraries = [
        path
        for path in root.rglob("*.so")
        if re.fullmatch(r"\.[0-9a-f]{16}-[0-9a-f]{8}\.so", path.name)
        and path.is_file()
        and not path.is_symlink()
        and path.stat().st_uid == os.getuid()
        and path.stat().st_nlink == 1
    ]
    lsof = shutil.which("lsof")
    if not libraries or lsof is None:
        return
    observed = subprocess.run(
        [lsof, "-nP", "--", *map(str, libraries)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if observed.returncode == 1 and not observed.stdout.strip():
        for path in libraries:
            path.unlink()


@contextmanager
def native_composer(
    root: Path, *, keybinds: Mapping[str, str] | None = None, expect_started: bool = True
) -> Iterator[NativeComposer]:
    """Run and reap one native client; never touch the owner's tmux server."""
    binary = find_opencode()
    tmux = shutil.which("tmux")
    if tmux is None:
        raise AssertionError("native composer acceptance requires tmux")
    home, workspace = root / "home", root / "workspace"
    for path in (root, home, workspace, root / "tmp", root / "runtime"):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = (tmux, "-L", "syn-composer-" + uuid.uuid4().hex, "-f", "/dev/null")
    wrapper = root / "tmux"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.join(base) + ' "$@"\n', encoding="utf-8")
    wrapper.chmod(0o700)
    config = AgentTmuxConfig(
        "E2E/native-composer",
        "agent",
        workspace,
        agent_command=(binary, "--mini", "--model", "test/test-model"),
        tmux_bin=str(wrapper),
        registry_dir=root / "runtime" / "synapse-agent-tmux",
        opencode_editor_readback=True,
    )
    try:
        with ScriptedLlmServer() as provider:
            environment = _native_environment(home, root, provider, keybinds)
            subprocess.run(
                [*base, "new-session", "-d", "-s", "anchor", "sleep 180"],
                env=environment,
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            subprocess.run(
                [*base, "set-option", "-g", "remain-on-exit", "on"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            fixture = NativeComposer(root, base, config, provider)
            started = start_session(config)
            assert started.started is expect_started, started.detail
            fixture = replace(fixture, start_result=started)
            if expect_started:
                assert started.returncode == 0, started.detail
                fixture.wait(lambda: "Ask anything..." in fixture.screen())
            else:
                assert started.returncode != 0
            yield fixture
    finally:
        subprocess.run([*base, "kill-server"], capture_output=True, check=False, timeout=10)
        remaining = subprocess.run(
            [*base, "list-sessions"], capture_output=True, check=False, timeout=10
        )
        assert remaining.returncode != 0, "owned native tmux server was not reaped"
        _release_extracted_libraries(root)
