# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — real native OpenCode composer qualification
"""Keep input preservation and single-submit behaviour on the native boundary."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fixtures.opencode.composer import controller_config_code, native_composer
from synapse_channel.agent_tmux import inject_wake, registry_path, start_session, status
from synapse_channel.opencode_composer import PROFILE_ENV
from synapse_channel.opencode_composer_files import write_document
from synapse_channel.opencode_composer_probe import footer_is_probeable, profile_directory


def test_long_native_reply_settles_the_verified_wake_after_it_scrolls_out(tmp_path: Path) -> None:
    """A large completed reply settles one submitted wake without another paste."""
    with native_composer(tmp_path) as native:
        native.seed()
        native.provider.enqueue_text("\n\n".join(f"RESPONSE_ROW_{i}" for i in range(80)))
        waits: list[float] = []

        def wait_for_render(delay: float) -> None:
            """Wait for the actual completed native viewport after the real submit."""
            waits.append(delay)
            if len(waits) == 1:
                time.sleep(delay)
            else:
                native.wait(
                    lambda: (
                        len(native.provider.prompt_requests) == 2
                        and "RESPONSE_ROW_79" in native.screen()
                        and "Synapse wake for" not in native.screen()
                        and footer_is_probeable(native.screen())
                    )
                )

        result = inject_wake(native.config, sleeper=wait_for_render)
        assert result.returncode == 0 and result.injected, result.detail
        assert len(waits) == 2
        assert "Synapse wake for" not in native.screen()
        record = json.loads(registry_path(native.config).read_text())
        assert record["pending_wake"] is False and record["wake_prompt_staged"] is False
        assert len(native.provider.prompt_requests) == 2


def test_verified_pending_wake_survives_a_temporarily_unbound_editor(tmp_path: Path) -> None:
    """Lost readback after a controller crash preserves the staged input until recovery."""
    import signal
    import sys

    with native_composer(tmp_path) as native:
        native.seed()
        native.provider.enqueue_text("RECOVERED_AFTER_PROFILE_RETURN")
        config = native.config
        child = (
            controller_config_code(config) + "def run(args,**kwargs):\n"
            " if args[1]=='send-keys' and args[-1]=='Enter':\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " return subprocess.run(args,**kwargs,timeout=10)\n"
            "inject_wake(cfg,runner=run)\n"
        )
        crashed = subprocess.run(
            [sys.executable, "-B", "-c", child],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert crashed.returncode == -signal.SIGKILL, crashed.stderr
        root = profile_directory(config)
        stage = json.loads((root / "stage.json").read_text())
        assert stage["verified"] is True
        request = (root / "request.json").read_bytes()
        native.tmux("set-environment", "-t", config.session, PROFILE_ENV, "unavailable")
        commands: list[tuple[str, ...]] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Observe real refusal and recovery commands, preserving their results."""
            assert text
            commands.append(tuple(args))
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        deferred = inject_wake(config, runner=run)
        assert deferred.returncode == 0 and not deferred.injected
        record = json.loads(registry_path(config).read_text())
        assert record["pending_wake"] is True and record["wake_prompt_staged"] is True
        assert json.loads((root / "stage.json").read_text())["verified"] is True
        assert (root / "request.json").read_bytes() == request
        assert not any(c[1] in {"send-keys", "paste-buffer"} for c in commands)
        assert len(native.provider.prompt_requests) == 1
        native.tmux("set-environment", "-t", config.session, PROFILE_ENV, str(root))
        commands.clear()
        recovered = inject_wake(config, runner=run)
        assert recovered.returncode == 0 and recovered.injected, recovered.detail
        assert not any(c[1] == "paste-buffer" for c in commands)
        assert sum(c[1] == "send-keys" and c[-1] == "Enter" for c in commands) == 1
        native.wait_requests(2)
        assert len(native.provider.prompt_requests) == 2


def test_loaded_native_session_consumes_each_wake_once(tmp_path: Path) -> None:
    """Historical Thinking does not block a proved empty current composer."""
    with native_composer(tmp_path) as native:
        native.seed()
        request = profile_directory(native.config) / "request.json"
        original_request = request.read_bytes()
        snapshot = status(native.config)
        assert snapshot.pane_state == "readback-required"
        assert snapshot.compatibility_aligned
        assert request.read_bytes() == original_request
        for count in (2, 3):
            native.provider.enqueue_text(f"NATIVE_WAKE_{count}_COMPLETED")
            result = inject_wake(native.config)
            assert result.returncode == 0 and result.injected, result.detail
            native.wait_requests(count)
            record = json.loads(registry_path(native.config).read_text())
            assert record["pending_wake"] is False
            assert record["wake_prompt_staged"] is False


@pytest.mark.parametrize("draft", [" ", "owner line\n", "first\nsecond\n"])
def test_native_wake_preserves_exact_owner_draft(tmp_path: Path, draft: str) -> None:
    """Whitespace and trailing newlines survive an actual refused wake."""
    with native_composer(tmp_path) as native:
        native.seed()
        native.paste(draft)
        result = inject_wake(native.config)
        assert result.returncode == 0 and not result.injected, result.detail
        before = json.loads((profile_directory(native.config) / "response.json").read_text())
        assert before["byte_count"] == len(draft.encode())
        assert before["sha256"] == hashlib.sha256(draft.encode()).hexdigest()
        assert native.readback().matches(draft)
        assert len(native.provider.prompt_requests) == 1


def test_native_shell_and_menu_refuse_without_editor_keys(tmp_path: Path) -> None:
    """Mode guards run before a callback request or native shortcut."""
    with native_composer(tmp_path) as native:
        native.seed()
        root = profile_directory(native.config)
        original_request = (root / "request.json").read_bytes()
        native.text("!")
        native.wait(lambda: "SHELL" in native.screen())
        shell = inject_wake(native.config)
        assert not shell.injected and shell.returncode == 0
        assert (root / "request.json").read_bytes() == original_request
        native.keys("Escape")
        native.wait(lambda: "BUILD" in native.screen() and "SHELL" not in native.screen())
        native.keys("C-p")
        native.wait(lambda: "Commands" in native.screen() and "/editor" in native.screen())
        menu = inject_wake(native.config)
        assert not menu.injected and menu.returncode == 0
        assert (root / "request.json").read_bytes() == original_request
        assert len(native.provider.prompt_requests) == 1


def test_foreign_native_binding_refuses_before_editor(tmp_path: Path) -> None:
    """A different live session owner cannot admit an editor probe."""
    with native_composer(tmp_path) as native:
        native.seed()
        request = profile_directory(native.config) / "request.json"
        original_request = request.read_bytes()
        native.tmux("set-environment", "-t", native.config.session, "SYN_IDENTITY", "E2E/other")
        result = inject_wake(native.config)
        assert result.returncode == 4 and not result.injected
        assert request.read_bytes() == original_request
        assert len(native.provider.prompt_requests) == 1


@pytest.mark.parametrize("crash_at", ["after-paste", "before-enter"])
@pytest.mark.parametrize("restart_at", ["returned", "after-publication"])
def test_pending_wake_restarts_in_the_new_native_process(
    tmp_path: Path, crash_at: str, restart_at: str
) -> None:
    """A newly qualified client receives a wake left in a retired native process."""
    import signal
    import sys

    with native_composer(tmp_path) as native:
        native.seed()
        config = native.config
        root = profile_directory(config)
        previous = json.loads((root / "binding.json").read_text())
        child = (
            controller_config_code(config) + "def run(args,**kwargs):\n"
            f" if {crash_at!r}=='before-enter' and args[1]=='send-keys' and args[-1]=='Enter':\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " result=subprocess.run(args,**kwargs,timeout=10)\n"
            f" if {crash_at!r}=='after-paste' and args[1]=='paste-buffer' "
            "and result.returncode==0:\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " return result\ninject_wake(cfg,runner=run)\n"
        )
        crashed = subprocess.run(
            [sys.executable, "-B", "-c", child],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert crashed.returncode == -signal.SIGKILL, crashed.stderr
        pending = json.loads(registry_path(config).read_text())
        assert pending["pending_wake"] is True and pending["wake_prompt_staged"] is True
        assert json.loads((root / "stage.json").read_text())["verified"] is (
            crash_at == "before-enter"
        )
        profile = json.loads((root / "profile.json").read_text())
        request = (root / "request.json").read_bytes()
        write_document(root, "profile.json", {**profile, "identity": "E2E/another-seat"})
        refused = inject_wake(config)
        assert refused.returncode == 0 and not refused.injected, refused.detail
        assert (root / "request.json").read_bytes() == request
        assert len(native.provider.prompt_requests) == 1
        write_document(root, "profile.json", profile)
        pending = json.loads(registry_path(config).read_text())
        native.tmux("kill-session", "-t", config.session)
        if restart_at == "after-publication":
            startup_child = (
                child.split("def run", 1)[0]
                + "from synapse_channel.agent_tmux import start_session\n"
                "def run(args,**kwargs):\n"
                " result=subprocess.run(args,**kwargs,timeout=10)\n"
                " if args[1]=='rename-session' and result.returncode==0:\n"
                "  os.kill(os.getpid(),signal.SIGKILL)\n"
                " return result\nstart_session(cfg,runner=run)\n"
            )
            published = subprocess.run(
                [sys.executable, "-B", "-c", startup_child],
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert published.returncode == -signal.SIGKILL, published.stderr
            assert json.loads(registry_path(config).read_text()) == pending
        restarted = start_session(config)
        assert restarted.started is (restart_at == "returned"), restarted.detail
        assert restarted.returncode == 0, restarted.detail
        current = json.loads((root / "binding.json").read_text())
        assert current["nonce"] != previous["nonce"]
        assert current["native_pid"] != previous["native_pid"]
        assert len(native.provider.prompt_requests) == 1
        native.provider.enqueue_text("RECOVERED_ON_NEW_NATIVE_PROCESS")
        commands: list[tuple[str, ...]] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Record actual native controls without replacing their results."""
            commands.append(tuple(args))
            assert text
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        for unknown in (None, "invalid-launch-marker"):
            uncertain = {**pending, "wake_launch_nonce": unknown}
            registry_path(config).write_text(json.dumps(uncertain) + "\n")
            refused = inject_wake(config, runner=run)
            assert refused.returncode == 0 and not refused.injected, refused.detail
            assert not any(c[1] == "paste-buffer" or c[-1] == "Enter" for c in commands)
            assert len(native.provider.prompt_requests) == 1
            commands.clear()
        registry_path(config).write_text(json.dumps(pending) + "\n")
        recovered = inject_wake(config, runner=run)
        assert sum(c[1] == "paste-buffer" for c in commands) == 1, recovered.detail
        assert sum(c[1] == "send-keys" and c[-1] == "Enter" for c in commands) == 1
        assert recovered.returncode == 0 and recovered.injected, recovered.detail
        native.wait_requests(2)
        assert len(native.provider.prompt_requests) == 2
        settled = json.loads(registry_path(config).read_text())
        assert settled["pending_wake"] is False and settled["wake_prompt_staged"] is False


def test_verified_wake_does_not_acknowledge_a_replaced_native_pane(tmp_path: Path) -> None:
    """A terminal replacement cannot consume the retired process's staged hint."""
    import signal
    import sys

    with native_composer(tmp_path) as native:
        native.seed()
        config = native.config
        child = (
            controller_config_code(config) + "def run(args,**kwargs):\n"
            " if args[1]=='send-keys' and args[-1]=='Enter':\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " return subprocess.run(args,**kwargs,timeout=10)\n"
            "inject_wake(cfg,runner=run)\n"
        )
        crashed = subprocess.run(
            [sys.executable, "-B", "-c", child],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert crashed.returncode == -signal.SIGKILL, crashed.stderr
        root = profile_directory(config)
        proof = (root / "stage.json").read_bytes()
        assert json.loads(proof)["verified"] is True
        native.tmux("respawn-pane", "-k", "-t", config.session, "sleep 180")
        native.wait(lambda: not native.screen().strip())
        commands: list[tuple[str, ...]] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Observe the actual retired-process refusal without replacing OS results."""
            commands.append(tuple(args))
            assert text
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        result = inject_wake(config, runner=run)
        assert result.returncode == 0 and not result.injected, result.detail
        assert not any(c[1] in {"send-keys", "paste-buffer"} for c in commands)
        pending = json.loads(registry_path(config).read_text())
        assert pending["pending_wake"] is True and pending["wake_prompt_staged"] is True
        assert (root / "stage.json").read_bytes() == proof
        assert len(native.provider.prompt_requests) == 1


@pytest.mark.parametrize("stage_document", ["present", "missing", "unsafe"])
def test_native_crash_after_paste_recovers_without_repaste(
    tmp_path: Path, stage_document: str
) -> None:
    """A killed waker recovers its existing input with one actual submission."""
    import signal
    import sys

    with native_composer(tmp_path) as native:
        native.seed()
        native.provider.enqueue_text("NATIVE_RECOVERY_COMPLETED")
        config = native.config
        child = (
            controller_config_code(config) + "def run(args,**kwargs):\n"
            " result=subprocess.run(args,**kwargs)\n"
            " if args[1]=='paste-buffer' and result.returncode==0:\n"
            "  os.kill(os.getpid(),signal.SIGKILL)\n"
            " return result\ninject_wake(cfg,runner=run)\n"
        )
        crashed = subprocess.run(
            [sys.executable, "-c", child], capture_output=True, text=True, check=False, timeout=20
        )
        assert crashed.returncode == -signal.SIGKILL, crashed.stderr
        record = json.loads(registry_path(config).read_text())
        assert record["pending_wake"] is True and record["wake_prompt_staged"] is True
        stage = profile_directory(config) / "stage.json"
        if stage_document == "missing":
            stage.unlink()
        elif stage_document == "unsafe":
            stage.chmod(0o644)
        commands: list[list[str]] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Observe actual recovery commands without simulating their results."""
            commands.append(list(args))
            assert text
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        deadline = time.monotonic() + 12
        while True:
            recovered = inject_wake(config, runner=run)
            assert recovered.returncode == 0, recovered.detail
            if recovered.injected:
                break
            assert time.monotonic() < deadline, recovered.detail
            time.sleep(0.2)
        assert not any(args[1] == "paste-buffer" for args in commands)
        assert sum(args[1] == "send-keys" and args[-1] == "Enter" for args in commands) == 1
        native.wait(lambda: len(native.provider.prompt_requests) == 2)
        assert len(native.provider.prompt_requests) == 2


@pytest.mark.parametrize("fault", ["missing", "wrong-nonce", "wrong-pid", "wrong-start"])
def test_unqualified_binding_refuses_before_any_editor_keys(tmp_path: Path, fault: str) -> None:
    """A missing or stale process receipt cannot authorize an input transaction."""
    import os

    with native_composer(tmp_path) as native:
        native.seed()
        native.paste("owner draft\n")
        root = profile_directory(native.config)
        original = json.loads((root / "binding.json").read_text())
        invalid = dict(original)
        if fault == "missing":
            (root / "binding.json").unlink()
        else:
            if fault == "wrong-nonce":
                invalid["nonce"] = "0" * 32
            elif fault == "wrong-pid":
                invalid["native_pid"] = os.getpid()
            else:
                invalid["native_start"] = "replaced process"
            write_document(root, "binding.json", invalid)
        original_request = (root / "request.json").read_bytes()
        assert status(native.config).pane_state == "readback-unavailable"
        assert (root / "request.json").read_bytes() == original_request
        commands: list[list[str]] = []

        def run(
            args: Sequence[str],
            *,
            capture_output: bool = False,
            text: bool = False,
            check: bool = False,
            env: Mapping[str, str] | None = None,
        ) -> subprocess.CompletedProcess[str]:
            """Observe real native controls without substituting their results."""
            commands.append(list(args))
            assert text
            return subprocess.run(
                args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
            )

        result = inject_wake(native.config, runner=run)
        assert result.returncode == 0 and not result.injected
        assert not any(command[1] in {"send-keys", "paste-buffer"} for command in commands)
        write_document(root, "binding.json", original)
        assert native.readback().matches("owner draft\n")
        assert len(native.provider.prompt_requests) == 1


def test_native_keymap_receipt_tracks_loaded_process_not_later_config_files(tmp_path: Path) -> None:
    """Disk changes do not rewrite the already qualified native keymap."""
    with native_composer(tmp_path, keybinds={"leader": "ctrl+x"}) as native:
        native.seed()
        binding = (profile_directory(native.config) / "binding.json").read_bytes()
        (tmp_path / "tui.json").write_text(
            json.dumps({"keybinds": {"leader": "ctrl+a", "editor_open": "none"}})
        )
        native.provider.enqueue_text("QUALIFIED_LOADED_KEYMAP_COMPLETED")
        result = inject_wake(native.config)
        assert result.returncode == 0 and result.injected, result.detail
        native.wait_requests(2)
        assert (profile_directory(native.config) / "binding.json").read_bytes() == binding


def test_existing_session_and_initial_input_are_not_startup_probe_targets(tmp_path: Path) -> None:
    """Existing drafts stay untouched; resume/prompt options never launch a probe."""
    from dataclasses import replace

    with native_composer(tmp_path) as native:
        native.seed()
        native.paste("preserve existing draft\n")
        root = profile_directory(native.config)
        original_profile = (root / "profile.json").read_bytes()
        original_request = (root / "request.json").read_bytes()
        existing = start_session(native.config)
        assert existing.returncode == 0 and not existing.started
        assert (root / "profile.json").read_bytes() == original_profile
        assert (root / "request.json").read_bytes() == original_request
        for arguments in [
            ("--continue",),
            ("--session", "existing"),
            ("--prompt", "do not submit"),
        ]:
            other = replace(
                native.config,
                session="refused-startup",
                agent_command=(*native.config.agent_command, *arguments),
            )
            refused = start_session(other)
            assert refused.returncode == 4 and not refused.started
        assert native.readback().matches("preserve existing draft\n")
        assert len(native.provider.prompt_requests) == 1
        assert set(native.tmux("list-sessions", "-F", "#{session_name}").splitlines()) == {
            "anchor",
            native.config.session,
        }


def test_public_cli_starts_and_wakes_the_qualified_native_client(tmp_path: Path) -> None:
    """The real CLI wires the opt-in lifecycle through start, status and wake."""
    import os
    import shlex
    import sys

    from synapse_channel import cli as cli_module

    with native_composer(tmp_path) as native:
        native.tmux("kill-session", "-t", native.config.session)
        environment = os.environ.copy()
        environment.update(
            HOME=str(tmp_path / "home"),
            XDG_RUNTIME_DIR=str(tmp_path / "runtime"),
            SYNAPSE_AUTO_CONNECT="0",
            PYTHONPATH=str(Path(cli_module.__file__).resolve().parent.parent),
        )
        common = [
            "--identity",
            native.config.identity,
            "--session",
            native.config.session,
            "--cwd",
            str(native.config.cwd),
            "--agent-command",
            shlex.join(native.config.agent_command),
            "--tmux-bin",
            native.config.tmux_bin,
            "--opencode-editor-readback",
        ]

        def invoke(command: str) -> subprocess.CompletedProcess[str]:
            """Run a genuine candidate CLI subprocess against the private native pane."""
            return subprocess.run(
                [sys.executable, "-m", "synapse_channel.cli", "agent-tmux", command, *common],
                cwd=tmp_path,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=45,
            )

        started = invoke("start")
        assert started.returncode == 0, started.stderr + started.stdout
        request = profile_directory(native.config) / "request.json"
        original_request = request.read_bytes()
        observed = invoke("status")
        assert observed.returncode == 0, observed.stderr + observed.stdout
        assert "readback-required" in observed.stdout
        assert request.read_bytes() == original_request
        native.seed()
        native.provider.enqueue_text("PUBLIC_NATIVE_CLI_WAKE_COMPLETED")
        delivered = invoke("wake")
        assert delivered.returncode == 0, delivered.stderr + delivered.stdout
        assert "consumption observed" in delivered.stdout
        native.wait_requests(2)
        record = json.loads(registry_path(native.config).read_text())
        assert record["pending_wake"] is False and record["wake_prompt_staged"] is False
