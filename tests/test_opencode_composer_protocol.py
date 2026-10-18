# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — native editor protocol refusal boundaries
"""Exercise private files and the callback CLI without substituting OS results."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fixtures.opencode.composer import native_composer
from synapse_channel import opencode_composer
from synapse_channel.agent_tmux import status
from synapse_channel.opencode_composer import (
    EDITOR_ABORT,
    LAUNCH_ENV,
    PROFILE_ENV,
    PROTOCOL,
    SUPPORTED_VERSION,
    process_stamp,
    require_native_process,
)
from synapse_channel.opencode_composer_files import (
    DOCUMENT_LIMIT,
    DRAFT_LIMIT,
    ComposerError,
    probe_lock,
    read_document,
    read_native_draft,
    write_document,
    write_private_bytes,
)
from synapse_channel.opencode_composer_probe import inspect_editor_composer, profile_directory


def _callback_documents(
    tmp_path: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, str]]:
    """Prepare independent callback input and a credential-free environment."""
    temporary = tmp_path / "tmp"
    launch = "a" * 32
    profile: dict[str, object] = {
        "protocol": PROTOCOL,
        "identity": "E2E/protocol",
        "session": "owned-session",
        "version": SUPPORTED_VERSION,
        "launch_nonce": launch,
    }
    request: dict[str, object] = {
        "protocol": PROTOCOL,
        "identity": "E2E/protocol",
        "session": "owned-session",
        "nonce": "b" * 32,
        "deadline_ns": time.monotonic_ns() + 4_000_000_000,
        "pane_pid": os.getpid(),
    }
    environment = {
        "PATH": os.defpath,
        "HOME": str(tmp_path),
        "TMPDIR": str(temporary),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(Path(opencode_composer.__file__).resolve().parent.parent),
        PROFILE_ENV: str(tmp_path),
        LAUNCH_ENV: launch,
        "SYN_IDENTITY": "E2E/protocol",
        "SYN_PROJECT": "E2E",
    }
    for key in (
        "COVERAGE_PROCESS_CONFIG",
        "COVERAGE_PROCESS_START",
        "COVERAGE_FILE",
        "COVERAGE_RCFILE",
        "COVERAGE_CORE",
    ):
        if key in os.environ:
            environment[key] = os.environ[key]
    return profile, request, environment


def _corrupt_request(
    fault: str, profile: dict[str, object], request: dict[str, object], environment: dict[str, str]
) -> None:
    """Change one real callback boundary while keeping all other fields valid."""
    if fault == "profile-string":
        profile["identity"] = "E2E/\x1b[31muntrusted"
    elif fault == "launch-nonce":
        environment[LAUNCH_ENV] = "c" * 32
    elif fault == "profile-version":
        profile["version"] = "unsupported"
    elif fault.endswith("-environment"):
        key = {
            "profile-environment": PROFILE_ENV,
            "identity-environment": "SYN_IDENTITY",
            "project-environment": "SYN_PROJECT",
        }[fault]
        environment[key] = "unrelated"
    elif fault.startswith("request-"):
        request[fault.removeprefix("request-")] = "unrelated"
    elif fault == "expired":
        request["deadline_ns"] = time.monotonic_ns() - 1
    elif fault == "future-deadline":
        request["deadline_ns"] = time.monotonic_ns() + 60_000_000_000
    elif fault == "boolean-deadline":
        request["deadline_ns"] = True
    elif fault == "boolean-pane":
        request["pane_pid"] = True


@pytest.mark.parametrize(
    "fault",
    [
        "profile-string",
        "launch-nonce",
        "profile-version",
        "profile-environment",
        "identity-environment",
        "project-environment",
        "request-protocol",
        "request-identity",
        "request-session",
        "request-nonce",
        "expired",
        "future-deadline",
        "boolean-deadline",
        "boolean-pane",
        "unrelated-parent",
    ],
)
def test_callback_cli_rejects_unbound_requests_without_exporting_input(
    tmp_path: Path, fault: str
) -> None:
    """Hostile callback arguments cannot disclose input or accept an edit."""
    temporary = tmp_path / "tmp"
    temporary.mkdir(mode=0o700)
    draft = temporary / "123.md"
    original = b"owner draft with private content\n\n"
    draft.write_bytes(original)
    profile, request, environment = _callback_documents(tmp_path)
    _corrupt_request(fault, profile, request, environment)
    write_document(tmp_path, "profile.json", profile)
    write_document(tmp_path, "request.json", request)
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "synapse_channel.opencode_composer",
            "--profile",
            str(tmp_path),
            str(draft),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == EDITOR_ABORT
    assert completed.stdout == completed.stderr == ""
    assert draft.read_bytes() == original
    assert not (tmp_path / "response.json").exists()


@pytest.mark.parametrize("fault", ["array", "oversize", "public", "hardlink", "symlink"])
def test_protocol_documents_reject_unsafe_storage(tmp_path: Path, fault: str) -> None:
    """Protocol readers reject non-objects and files outside the private contract."""
    path = tmp_path / "request.json"
    write_document(tmp_path, path.name, {"nonce": "a" * 32})
    if fault == "array":
        path.write_text("[]")
    elif fault == "oversize":
        path.write_bytes(b" " * (DOCUMENT_LIMIT + 1))
    elif fault == "public":
        path.chmod(0o644)
    elif fault == "hardlink":
        os.link(path, tmp_path / "alias")
    else:
        target = tmp_path / "target.json"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(ValueError):
        read_document(path)


@pytest.mark.parametrize("fault", ["outside", "name", "oversize", "hardlink", "symlink"])
def test_native_export_reader_refuses_unsafe_files(tmp_path: Path, fault: str) -> None:
    """A callback cannot read another path, follow an alias or accept excess input."""
    temporary = tmp_path / "tmp"
    temporary.mkdir(mode=0o700)
    path = temporary / "123.md"
    content = b"owned draft\n"
    path.write_bytes(content)
    if fault == "outside":
        path = tmp_path / "123.md"
        path.write_bytes(content)
    elif fault == "name":
        path = temporary / "unrelated.md"
        path.write_bytes(content)
    elif fault == "oversize":
        path.write_bytes(b"x" * (DRAFT_LIMIT + 1))
    elif fault == "hardlink":
        os.link(path, temporary / "alias")
    else:
        target = temporary / "target.md"
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises((OSError, ValueError)):
        read_native_draft(tmp_path, path)
    assert path.read_bytes() == (b"x" * (DRAFT_LIMIT + 1) if fault == "oversize" else content)


@pytest.mark.parametrize("fault", ["name", "oversize", "directory-permissions"])
def test_profile_writer_refuses_without_replacing_existing_data(tmp_path: Path, fault: str) -> None:
    """Invalid writes leave the current profile and directory entries unchanged."""
    write_document(tmp_path, "profile.json", {"nonce": "original"})
    original = (tmp_path / "profile.json").read_bytes()
    name, data = "profile.json", b"replacement"
    if fault == "name":
        name = "../outside.json"
    elif fault == "oversize":
        data = b"x" * (DOCUMENT_LIMIT + 1)
    else:
        tmp_path.chmod(0o755)
    try:
        with pytest.raises(ComposerError):
            write_private_bytes(tmp_path, name, data)
        assert (tmp_path / "profile.json").read_bytes() == original
        assert set(path.name for path in tmp_path.iterdir()) == {"profile.json"}
    finally:
        tmp_path.chmod(0o700)


def test_probe_lock_refuses_contention_and_is_reusable_after_release(tmp_path: Path) -> None:
    """Another reader cannot enter a live transaction; released locks remain usable."""
    with probe_lock(tmp_path):
        with pytest.raises(ComposerError, match="another native composer probe is active"):
            with probe_lock(tmp_path):
                pytest.fail("a second reader entered the same transaction")
    with probe_lock(tmp_path):
        assert (tmp_path / "probe.lock").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ComposerError, match="lock name is invalid"):
        with probe_lock(tmp_path, name="../foreign.lock"):
            pytest.fail("an arbitrary lock name was accepted")


@pytest.mark.parametrize("fault", ["permissions", "hardlink", "symlink", "fifo"])
def test_probe_lock_refuses_replaced_lock_files(tmp_path: Path, fault: str) -> None:
    """A malicious alias or non-regular entry cannot become a transaction lock."""
    lock = tmp_path / "probe.lock"
    if fault == "fifo":
        os.mkfifo(lock, 0o600)
    elif fault == "symlink":
        target = tmp_path / "foreign.lock"
        target.touch(mode=0o600)
        lock.symlink_to(target)
    else:
        lock.touch(mode=0o600)
        if fault == "permissions":
            lock.chmod(0o644)
        else:
            os.link(lock, tmp_path / "alias.lock")
    with pytest.raises((OSError, ValueError)):
        with probe_lock(tmp_path):
            pytest.fail("an unsafe lock was acquired")


def test_process_stamp_rejects_a_reaped_process(tmp_path: Path) -> None:
    """A stale process receipt cannot be refreshed from a terminated child."""
    completed = subprocess.Popen(
        [sys.executable, "-c", "pass"], cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    completed.communicate(timeout=10)
    assert completed.returncode == 0
    with pytest.raises(ComposerError, match="identity is unavailable"):
        process_stamp(completed.pid)


@pytest.mark.parametrize("pid", [True, 0, 1, -1])
def test_process_stamp_refuses_non_process_identifiers(pid: int) -> None:
    """Sentinels and boolean metadata cannot become native process identities."""
    with pytest.raises(ComposerError, match="native process id is invalid"):
        process_stamp(pid)


def test_native_process_ancestry_has_a_finite_real_parent_boundary(tmp_path: Path) -> None:
    """An actual native process accepts its nearby parent but refuses a distant ancestor."""
    import shlex
    import signal

    with native_composer(tmp_path) as native:
        home, temporary = tmp_path / "descendant-home", tmp_path / "descendant-tmp"
        home.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
        configuration = home / ".config/opencode"
        configuration.mkdir(mode=0o500, parents=True)
        environment = dict(
            HOME=str(home),
            OPENCODE_TEST_HOME=str(home),
            XDG_CONFIG_HOME=str(home / ".config"),
            XDG_DATA_HOME=str(home / ".local/share"),
            XDG_STATE_HOME=str(home / ".local/state"),
            XDG_CACHE_HOME=str(home / ".cache"),
            TMPDIR=str(temporary),
            SYNAPSE_AUTO_CONNECT="0",
        )
        script, receipt = tmp_path / "descendant.py", tmp_path / "descendant-pid"
        binary = native.config.agent_command[0]
        script.write_text(
            "import os,subprocess,sys\nfrom pathlib import Path\n"
            "depth=int(sys.argv[1])\n"
            "if depth:\n"
            " child=subprocess.Popen([sys.executable,'-B',__file__,str(depth-1)])\n"
            " raise SystemExit(child.wait())\n"
            f"Path({str(receipt)!r}).write_text(str(os.getpid()))\n"
            f"os.execve({binary!r},[{binary!r},'--mini','--pure'],dict(os.environ))\n"
        )
        primary_pane = int(
            native.tmux("display-message", "-p", "-t", native.config.session, "#{pane_pid}")
        )
        primary = native.readback()
        require_native_process(primary.native_pid, primary.native_start, primary_pane)
        command = shlex.join(
            [
                "env",
                *(f"{k}={v}" for k, v in environment.items()),
                sys.executable,
                "-B",
                str(script),
                "12",
            ]
        )
        native.tmux("new-session", "-d", "-s", "descendant", "-c", str(tmp_path), command)
        pane = int(native.tmux("display-message", "-p", "-t", "descendant", "#{pane_pid}"))
        pid: int | None = None
        try:
            deadline = time.monotonic() + 10
            while True:
                if receipt.exists():
                    observed = receipt.read_text()
                    if observed.isdecimal():
                        pid = int(observed)
                        _, started, name = process_stamp(pid)
                        if name == "opencode":
                            break
                assert time.monotonic() < deadline, "the actual native descendant did not start"
                time.sleep(0.01)
            assert pid is not None
            os.kill(pid, signal.SIGSTOP)
            with pytest.raises(ComposerError, match="outside the requested pane"):
                require_native_process(pid, started, pane)
            assert not native.provider.prompt_requests
        finally:
            for owned in {pane, pid} - {None}:
                assert owned is not None
                try:
                    group = os.getpgid(owned)
                    assert group not in {os.getpgrp(), os.getpgid(primary_pane)}
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            native.tmux("kill-session", "-t", "descendant")


def test_process_stamp_is_stable_across_caller_timezones() -> None:
    """The same actual live process has one receipt across control environments."""
    observed: list[list[object]] = []
    script = (
        "import json;from synapse_channel.opencode_composer import process_stamp;"
        f"print(json.dumps(process_stamp({os.getpid()})))"
    )
    for zone in ("UTC0", "EST5"):
        result = subprocess.run(
            [sys.executable, "-B", "-c", script],
            env={**os.environ, "LC_ALL": "C", "TZ": zone},
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        observed.append(json.loads(result.stdout))
    assert observed[0] == observed[1]
    assert observed[0][0] == os.getppid()


def test_native_readback_preserves_input_across_controller_timezones(tmp_path: Path) -> None:
    """Controllers in different zones observe the same bound client and typed draft."""
    with native_composer(tmp_path) as native:
        native.seed()
        draft = "owner π draft across timezones\nsecond line"
        native.paste(draft)
        config = native.config
        script = (
            "from pathlib import Path;import subprocess;"
            "from synapse_channel.agent_tmux import AgentTmuxConfig;"
            "from synapse_channel.opencode_composer_probe import inspect_editor_composer;"
            f"config=AgentTmuxConfig({config.identity!r},{config.session!r},"
            f"Path({str(config.cwd)!r}),agent_command={config.agent_command!r},"
            f"tmux_bin={config.tmux_bin!r},"
            f"registry_dir=Path({str(config.registry_dir)!r}),opencode_editor_readback=True);"
            f"observed=inspect_editor_composer(config,{native.screen()!r},runner=subprocess.run);"
            f"assert observed.matches({draft!r})"
        )
        for zone in ("UTC0", "EST5"):
            result = subprocess.run(
                [sys.executable, "-B", "-c", script],
                env={**os.environ, "LC_ALL": "C", "TZ": zone},
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr
        assert native.readback().matches(draft)
        assert len(native.provider.prompt_requests) == 1


def _corrupt_finished_response(root: Path, fault: str) -> None:
    """Alter a real response only after its actual callback process has exited."""
    nonce = read_document(root / "request.json")["nonce"]
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        document = read_document(root / "response.json")
        if document.get("nonce") == nonce:
            pid = document["callback_pid"]
            assert isinstance(pid, int)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        time.sleep(0.01)
    else:
        pytest.fail("the real editor callback did not finish")
    if fault == "parent-start":
        document["native_start"] = "different native process"
    elif fault == "callback-start":
        document["callback_start"] = ""
    elif fault == "boolean-pid":
        document["native_pid"] = True
    elif fault == "size":
        document["byte_count"] = DRAFT_LIMIT + 1
    else:
        document["sha256"] = "untrusted digest"
    write_document(root, "response.json", document)


def test_native_readback_rejects_corrupted_receipts_and_preserves_the_draft(
    tmp_path: Path,
) -> None:
    """An invalid callback response never authorizes submission or loses native input."""
    with native_composer(tmp_path) as native:
        native.seed()
        draft = "owner π draft\nsecond line\n"
        native.paste(draft)
        root = profile_directory(native.config)
        for fault in ["parent-start", "callback-start", "boolean-pid", "size", "digest"]:

            def run(
                args: Sequence[str],
                *,
                capture_output: bool = False,
                text: bool = False,
                check: bool = False,
                env: Mapping[str, str] | None = None,
                fault: str = fault,
            ) -> subprocess.CompletedProcess[str]:
                """Execute genuine controls, then corrupt their completed private receipt."""
                assert text
                result = subprocess.run(
                    args, capture_output=capture_output, text=True, check=check, env=env, timeout=10
                )
                if args[1] == "send-keys":
                    _corrupt_finished_response(root, fault)
                return result

            with pytest.raises(ComposerError, match="native editor readback is unavailable"):
                inspect_editor_composer(native.config, native.screen(), runner=run)
            assert native.readback().matches(draft)
            assert len(native.provider.prompt_requests) == 1
        observed = native.readback()
        with pytest.raises(ComposerError, match="outside the requested pane"):
            require_native_process(observed.native_pid, observed.native_start, os.getpid())
        with pytest.raises(ComposerError, match="identity changed"):
            require_native_process(observed.native_pid, "stale process", observed.native_pid)
        original_profile = read_document(root / "profile.json")
        for fault in ["missing", "array", "nonce"]:
            if fault == "missing":
                (root / "profile.json").unlink()
            elif fault == "array":
                (root / "profile.json").write_text("[]")
            else:
                write_document(root, "profile.json", {**original_profile, "launch_nonce": True})
            original_request = (root / "request.json").read_bytes()
            assert status(native.config).pane_state == "readback-unavailable"
            assert (root / "request.json").read_bytes() == original_request
            write_document(root, "profile.json", original_profile)
        assert native.readback().matches(draft)
        assert len(native.provider.prompt_requests) == 1
