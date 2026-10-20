# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — qualify new native keymaps and recover owned launches
"""Keep unqualified keys away from existing clients and preserve launch ownership."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from fixtures.opencode.composer import native_composer
from synapse_channel.agent_tmux import start_session
from synapse_channel.opencode_composer import LAUNCH_ENV
from synapse_channel.opencode_composer_files import write_document
from synapse_channel.opencode_composer_probe import profile_directory


@pytest.mark.parametrize("race", ["create-conflict", "disappears", "publish-conflict"])
def test_startup_control_races_preserve_other_sessions(tmp_path: Path, race: str) -> None:
    """Real launch or rename failures never retire a session acquired by another owner."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        retained: list[str] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Interleave real tmux operations without changing their returned results."""
            assert text
            if race == "create-conflict" and args[1] == "new-session":
                target = args[args.index("-s") + 1]
                native.tmux("new-session", "-d", "-s", target, "sleep 60")
                retained.append(target)
            elif race == "publish-conflict" and args[1] == "rename-session":
                native.tmux("new-session", "-d", "-s", native.config.session, "sleep 60")
                retained.append(native.config.session)
            result = subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )
            if race == "disappears" and args[1] == "new-session" and result.returncode == 0:
                native.tmux("kill-session", "-t", args[args.index("-s") + 1])
            return result

        refused = start_session(native.config, runner=run)
        assert not refused.started and refused.returncode != 0
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            *retained,
        }
        for target in retained:
            pid = int(native.tmux("display-message", "-p", "-t", target, "#{pane_pid}"))
            os.kill(pid, 0)
            assert native.tmux("display-message", "-p", "-t", target, "#{pane_dead}").strip() == "0"
        assert not native.provider.prompt_requests


@pytest.mark.parametrize("ownership", ["original", "changed", "missing", "moved"])
def test_nonempty_startup_retires_only_its_unchanged_owner(tmp_path: Path, ownership: str) -> None:
    """Nonempty startup refuses publication and preserves ownership acquired during cleanup."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        edited: list[str] = []
        draft = "unpublished owner draft"

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Interleave actual input and metadata/rename changes on the private target."""
            assert text
            if args[1] == "send-keys":
                target = args[args.index("-t") + 1]
                native.tmux("set-buffer", "-b", "startup-race", "--", draft)
                native.tmux("paste-buffer", "-b", "startup-race", "-d", "-p", "-t", target)
                native.wait(lambda: draft in native.tmux("capture-pane", "-t", target, "-p", "-J"))
                edited.append(target)
            elif edited and args[1] == "show-environment" and args[-1] == LAUNCH_ENV:
                if ownership == "changed":
                    native.tmux("set-environment", "-t", edited[0], LAUNCH_ENV, "e" * 32)
                elif ownership == "missing":
                    native.tmux("set-environment", "-u", "-t", edited[0], LAUNCH_ENV)
            elif edited and args[1] == "kill-session" and ownership == "moved":
                native.tmux("rename-session", "-t", edited[0], "retained-native")
                edited[0] = "retained-native"
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        refused = start_session(native.config, runner=run)
        assert not refused.started and refused.returncode == 4
        assert edited
        response = json.loads((profile_directory(native.config) / "response.json").read_text())
        assert response["byte_count"] == len(draft.encode())
        assert response["sha256"] == hashlib.sha256(draft.encode()).hexdigest()
        sessions = set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines())
        assert sessions == ({"anchor"} if ownership == "original" else {"anchor", edited[0]})
        if ownership != "original":
            pid = int(native.tmux("display-message", "-p", "-t", edited[0], "#{pane_pid}"))
            os.kill(pid, 0)
            assert draft in native.tmux("capture-pane", "-t", edited[0], "-p", "-J")
        assert not native.provider.prompt_requests


def test_invalid_native_configuration_reaches_a_finite_startup_refusal(tmp_path: Path) -> None:
    """A real client configuration error cannot publish a qualified terminal."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        native.tmux("set-environment", "-g", "OPENCODE_CONFIG_CONTENT", "{")
        started_at = time.monotonic()
        refused = start_session(native.config)
        assert refused.returncode == 4 and not refused.started
        assert time.monotonic() - started_at < 40
        assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]
        assert not native.provider.prompt_requests


def test_native_control_timeout_reaps_the_command_and_allows_a_fresh_start(
    tmp_path: Path,
) -> None:
    """An actual blocked capture retires only its unpublished launch and can recover."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        wrapper = Path(native.config.tmux_bin)
        original = wrapper.read_text(encoding="utf-8")
        marker = tmp_path / "stalled-control.pid"
        wrapper.write_text(
            f"#!{sys.executable}\nimport os,sys,time\nfrom pathlib import Path\n"
            "if sys.argv[1] == 'capture-pane':\n"
            f" Path({str(marker)!r}).write_text(str(os.getpid()))\n"
            " time.sleep(30)\n"
            f"os.execv({native.base[0]!r}, [*{native.base!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )
        try:
            started_at = time.monotonic()
            refused = start_session(native.config)

            assert refused.returncode == 4 and not refused.started
            assert time.monotonic() - started_at < 20
            stalled_pid = int(marker.read_text())
            with pytest.raises(ProcessLookupError):
                os.kill(stalled_pid, 0)
            assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]
            assert not native.provider.prompt_requests
        finally:
            wrapper.write_text(original, encoding="utf-8")

        recovered = start_session(native.config)
        assert recovered.returncode == 0 and recovered.started, recovered.detail
        assert native.readback().matches(None)
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            native.config.session,
        }
        assert not native.provider.prompt_requests


def test_new_client_admission_refuses_input_and_unrepresentable_profiles(tmp_path: Path) -> None:
    """Rejected commands preserve the old profile and never launch or submit input."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        root = profile_directory(native.config)
        original = {p.name: p.read_bytes() for p in root.glob("*.json")}
        binary = native.config.agent_command[0]
        commands = [
            (),
            (binary, "--mini", "--prompt", "owner input"),
            (binary, "--mini", "--continue"),
            (binary, "--mini", "--session", "existing-session"),
            (binary, "--mini", "--model"),
            (binary, "--mini", "--model", "--pure"),
            (binary, "--mini", "--model="),
            ("/absent/native/opencode", "--mini"),
            (sys.executable, "--mini"),
        ]
        for command in commands:
            refused = start_session(replace(native.config, agent_command=command))
            assert refused.returncode == 4 and not refused.started
            assert {p.name: p.read_bytes() for p in root.glob("*.json")} == original
            assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]
            assert not native.provider.prompt_requests
        registry = tmp_path / "profile with whitespace"
        spaced = replace(native.config, registry_dir=registry)
        refused = start_session(spaced)
        assert refused.returncode == 4 and not refused.started
        refused_profile = profile_directory(spaced)
        assert {p.name for p in refused_profile.iterdir()} == {"wake.lock"}
        assert refused_profile.stat().st_mode & 0o777 == 0o700
        assert (refused_profile / "wake.lock").stat().st_mode & 0o777 == 0o600
        assert {p.name: p.read_bytes() for p in root.glob("*.json")} == original
        assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]
        assert not native.provider.prompt_requests


def test_new_client_accepts_an_inline_model_option(tmp_path: Path) -> None:
    """A supported inline option still qualifies the actual empty native client."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        config = replace(
            native.config,
            agent_command=(native.config.agent_command[0], "--mini", "--model=test/test-model"),
        )
        started = start_session(config)
        assert started.started and started.returncode == 0, started.detail
        assert native.readback().matches(None)
        assert not native.provider.prompt_requests


def test_new_native_shortcut_is_qualified_before_target_publication(tmp_path: Path) -> None:
    """Startup proves empty input on the same process without a model request."""
    with native_composer(tmp_path) as native:
        root = profile_directory(native.config)
        binding = json.loads((root / "binding.json").read_text())
        profile = json.loads((root / "profile.json").read_text())
        assert binding["nonce"] == profile["launch_nonce"]
        assert binding["shortcut"] == "C-x e"
        assert binding["byte_count"] == 0
        assert binding["sha256"] == hashlib.sha256(b"").hexdigest()
        assert native.readback().native_pid == binding["native_pid"]
        assert not native.provider.prompt_requests
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            native.config.session,
        }


@pytest.mark.parametrize(
    "keybinds",
    [
        {"leader": "ctrl+a"},
        {"editor_open": "none"},
        {"editor_open": "ctrl+e"},
    ],
)
def test_custom_editor_binding_refuses_unpublished_startup(
    tmp_path: Path, keybinds: dict[str, str]
) -> None:
    """An unknown shortcut only reaches its own new unpublished client."""
    with native_composer(tmp_path, keybinds=keybinds, expect_started=False) as native:
        assert native.start_result is not None
        assert native.start_result.returncode == 4
        assert not native.provider.prompt_requests
        assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]


def test_competing_start_cannot_replace_the_winners_live_profile(tmp_path: Path) -> None:
    """An actual competing launch wins between a missing-target check and admission."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        winner: dict[str, bytes] = {}

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Interleave a real winner after the real outer missing-target result."""
            assert text
            result = subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )
            if args[1] == "has-session" and result.returncode and not winner:
                root = profile_directory(native.config)
                started = start_session(native.config)
                assert started.returncode == 0 and started.started, started.detail
                winner["profile"] = (root / "profile.json").read_bytes()
                winner["binding"] = (root / "binding.json").read_bytes()
            return result

        loser = start_session(native.config, runner=run)
        assert loser.returncode == 4 and not loser.started
        root = profile_directory(native.config)
        assert (root / "profile.json").read_bytes() == winner["profile"]
        assert (root / "binding.json").read_bytes() == winner["binding"]
        assert native.readback().matches(None)
        assert not native.provider.prompt_requests
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            native.config.session,
        }


def test_killed_startup_controller_recovers_only_its_unpublished_client(tmp_path: Path) -> None:
    """A real SIGKILL after tmux creation leaves one recoverable owned intent."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        config = native.config
        script = (
            "from pathlib import Path\nimport os,signal,subprocess\n"
            "from synapse_channel.agent_tmux import AgentTmuxConfig,start_session\n"
            f"cfg=AgentTmuxConfig({config.identity!r},{config.session!r},"
            f"Path({str(config.cwd)!r}),agent_command={config.agent_command!r},"
            f"tmux_bin={config.tmux_bin!r},registry_dir=Path({str(config.registry_dir)!r}),"
            "opencode_editor_readback=True)\n"
            "def run(args,**kwargs):\n"
            " result=subprocess.run(args,**kwargs,timeout=10)\n"
            " if args[1]=='new-session' and result.returncode==0:\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " return result\nstart_session(cfg,runner=run)\n"
        )
        crashed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert crashed.returncode == -signal.SIGKILL, crashed.stderr
        root = profile_directory(config)
        old_intent = json.loads((root / "startup.json").read_text())
        assert old_intent["tmux_bin"] == config.tmux_bin
        old_target = old_intent["target"]
        assert old_target in native.tmux("list-sessions", "-F", "#{session_name}").splitlines()
        old_pid = int(native.tmux("display-message", "-p", "-t", old_target, "#{pane_pid}"))
        recovered = start_session(config)
        assert recovered.returncode == 0 and recovered.started, recovered.detail
        assert json.loads((root / "startup.json").read_text())["nonce"] != old_intent["nonce"]
        assert native.readback().matches(None)
        assert not native.provider.prompt_requests
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            config.session,
        }
        deadline = time.monotonic() + 3
        while True:
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                break
            assert time.monotonic() < deadline, "owned unpublished pane process remained alive"
            time.sleep(0.05)


def test_recovery_refuses_a_changed_controller_without_overwriting_its_intent(
    tmp_path: Path,
) -> None:
    """A journal for another tmux controller cannot authorize cleanup or a launch."""
    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        root = profile_directory(native.config)
        original = json.loads((root / "startup.json").read_text())
        invalid = {**original, "tmux_bin": "/different/controller"}
        write_document(root, "startup.json", invalid)
        original_profile = (root / "profile.json").read_bytes()
        refused = start_session(native.config)
        assert refused.returncode == 4 and not refused.started
        assert json.loads((root / "startup.json").read_text()) == invalid
        assert (root / "profile.json").read_bytes() == original_profile
        assert native.tmux("list-sessions", "-F", "#{session_name}").splitlines() == ["anchor"]
        assert not native.provider.prompt_requests
