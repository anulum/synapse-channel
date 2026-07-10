# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the TPM 2.0 at-rest key backend, exercised against swtpm

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import (
    WRAPPED_KEY_SCHEMA,
    generate_wrapped_key_file,
)
from synapse_channel.core.at_rest_tpm2 import (
    DEFAULT_TPM2_TCTI,
    TEMPLATE_VERSION,
    TPM2_BACKEND,
    cipher_from_wrapped_key_file_tpm2,
    generate_wrapped_key_file_tpm2,
)


def _tpm2_available() -> bool:
    """Return whether tpm2-pytss and a swtpm software-TPM rig are all present."""
    try:
        import tpm2_pytss  # noqa: F401
    except ImportError:
        return False
    return shutil.which("swtpm") is not None and shutil.which("swtpm_setup") is not None


# The backend needs a live TPM. CI installs tpm2-pytss + swtpm so these run and count toward
# coverage; elsewhere they skip gracefully rather than erroring on a missing device.
pytestmark = pytest.mark.skipif(
    not _tpm2_available(),
    reason="requires tpm2-pytss + swtpm (installed in CI)",
)


def _free_adjacent_tcp_ports() -> tuple[int, int]:
    """Return a free ``(server, server + 1)`` port pair for the swtpm socket.

    The swtpm TCTI derives the control port as the server port plus one, so the two must be
    adjacent rather than independently chosen.
    """
    for _ in range(64):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            base = int(probe.getsockname()[1])
        if base >= 65535:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as ctrl:
                ctrl.bind(("127.0.0.1", base + 1))
        except OSError:  # pragma: no cover - the adjacent port was momentarily taken; retry.
            continue
        return base, base + 1
    raise RuntimeError("could not find an adjacent free TCP port pair for swtpm")


def _wait_for_port(host: str, port: int, *, timeout: float = 10.0) -> None:
    """Block until ``host:port`` accepts a connection, or raise once ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"swtpm did not open {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def swtpm_tcti(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Start one isolated swtpm software TPM for the session and yield its TCTI string.

    The state directory lives under the pytest tmp base (``/tmp``-rooted): a local AppArmor profile
    denies swtpm state dirs on the working volume, and ``/tmp`` is always permitted.
    """
    state = tmp_path_factory.mktemp("swtpm-state")
    subprocess.run(  # noqa: S603 - fixed argv, no shell, test-only TPM setup
        ["swtpm_setup", "--tpm2", "--tpmstate", str(state), "--overwrite"],
        check=True,
        capture_output=True,
    )
    server_port, ctrl_port = _free_adjacent_tcp_ports()
    proc = subprocess.Popen(  # noqa: S603 - fixed argv, no shell, test-only TPM socket
        [
            "swtpm",
            "socket",
            "--tpm2",
            "--tpmstate",
            f"dir={state}",
            "--ctrl",
            f"type=tcp,port={ctrl_port}",
            "--server",
            f"type=tcp,port={server_port}",
            "--flags",
            "not-need-init,startup-clear",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port("127.0.0.1", server_port)
        yield f"swtpm:host=127.0.0.1,port={server_port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover - swtpm exits promptly on SIGTERM.
            proc.kill()


def test_generate_wraps_in_tpm_and_round_trips(swtpm_tcti: str, tmp_path: Path) -> None:
    key_path = tmp_path / "tpm.wrapped.key"
    generate_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    document = json.loads(key_path.read_text(encoding="utf-8"))
    assert document["schema"] == WRAPPED_KEY_SCHEMA
    assert document["backend"] == TPM2_BACKEND
    assert document["params"] == {"template_version": TEMPLATE_VERSION}

    cipher = cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    blob = cipher.encrypt(b"tpm-wrapped secret")
    # A second, independent load re-derives the same primary and unwraps the same data key.
    reopened = cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    assert reopened.decrypt(blob) == b"tpm-wrapped secret"


def test_two_independent_loads_share_the_data_key(swtpm_tcti: str, tmp_path: Path) -> None:
    # The deterministic primary means two separate loads of one file yield the same data key, so a
    # blob sealed by one cipher decrypts under the other — the property a hub restart relies on.
    key_path = tmp_path / "shared.key"
    generate_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    first = cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    second = cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    assert second.decrypt(first.encrypt(b"shared")) == b"shared"


def test_generate_refuses_to_overwrite(swtpm_tcti: str, tmp_path: Path) -> None:
    key_path = tmp_path / "tpm.wrapped.key"
    generate_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    with pytest.raises(FileExistsError):
        generate_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)


def test_load_rejects_a_non_tpm2_file(swtpm_tcti: str, tmp_path: Path) -> None:
    passphrase_file = tmp_path / "pp.wrapped.key"
    generate_wrapped_key_file(passphrase_file, "pw", n=2**10)
    with pytest.raises(ValueError, match="not TPM2"):
        cipher_from_wrapped_key_file_tpm2(passphrase_file, tcti=swtpm_tcti)


def test_load_rejects_malformed_tpm2_params(swtpm_tcti: str, tmp_path: Path) -> None:
    key_path = tmp_path / "malformed.key"
    key_path.write_text(
        json.dumps(
            {
                "schema": WRAPPED_KEY_SCHEMA,
                "backend": TPM2_BACKEND,
                "params": {},  # no template_version
                "wrapped_key": "AAAA",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed TPM2 wrapped at-rest key file"):
        cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)


def test_load_rejects_an_unsupported_template_version(swtpm_tcti: str, tmp_path: Path) -> None:
    key_path = tmp_path / "future.key"
    key_path.write_text(
        json.dumps(
            {
                "schema": WRAPPED_KEY_SCHEMA,
                "backend": TPM2_BACKEND,
                "params": {"template_version": TEMPLATE_VERSION + 1},
                "wrapped_key": "AAAA",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsupported TPM2 key template version"):
        cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)


def _tpm2_cli_args(path: Path, *extra: str) -> list[str]:
    return ["encrypt-key", "generate-wrapped-tpm2", *extra, str(path)]


def test_cli_generate_wrapped_tpm2_round_trips_and_refuses_overwrite(
    swtpm_tcti: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from synapse_channel import cli, cli_encrypt_key_hardware

    key_path = tmp_path / "cli.tpm.key"
    args = cli.build_parser().parse_args(_tpm2_cli_args(key_path, "--tcti", swtpm_tcti))
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(args) == 0
    assert "TPM-wrapped at-rest key" in capsys.readouterr().out

    cipher = cipher_from_wrapped_key_file_tpm2(key_path, tcti=swtpm_tcti)
    assert cipher.decrypt(cipher.encrypt(b"z")) == b"z"

    again = cli.build_parser().parse_args(_tpm2_cli_args(key_path, "--tcti", swtpm_tcti))
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(again) == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_cli_resolves_tcti_from_env_then_the_device_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resolution order is --tcti, then TPM2_TCTI, then the device default. The token operation is
    # stubbed so only the CLI's resolution logic is exercised, independent of a live TPM.
    from synapse_channel import cli, cli_encrypt_key_hardware
    from synapse_channel.core import at_rest_tpm2

    seen: list[str] = []

    def _record(path: str, *, tcti: str) -> Path:
        seen.append(tcti)
        return Path(path)

    monkeypatch.setattr(at_rest_tpm2, "generate_wrapped_key_file_tpm2", _record)

    monkeypatch.setenv("TPM2_TCTI", "env:tcti")
    env_args = cli.build_parser().parse_args(_tpm2_cli_args(tmp_path / "e.key"))
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(env_args) == 0

    monkeypatch.delenv("TPM2_TCTI", raising=False)
    default_args = cli.build_parser().parse_args(_tpm2_cli_args(tmp_path / "d.key"))
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(default_args) == 0

    assert seen == ["env:tcti", DEFAULT_TPM2_TCTI]


def test_cli_reports_a_missing_dependency(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from synapse_channel import cli, cli_encrypt_key_hardware
    from synapse_channel.core import at_rest_tpm2

    def _raise(path: str, *, tcti: str) -> Path:
        raise RuntimeError("the TPM 2.0 at-rest key backend requires the optional 'tpm2-pytss'")

    monkeypatch.setattr(at_rest_tpm2, "generate_wrapped_key_file_tpm2", _raise)
    args = cli.build_parser().parse_args(_tpm2_cli_args(tmp_path / "x.key", "--tcti", "swtpm:x"))
    assert cli_encrypt_key_hardware._cmd_generate_wrapped_tpm2(args) == 2
    assert "generate-wrapped-tpm2" in capsys.readouterr().out
