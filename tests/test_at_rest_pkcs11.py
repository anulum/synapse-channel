# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE_CHANNEL — tests for the PKCS#11 at-rest key backend, exercised against SoftHSM2

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from synapse_channel.core.at_rest import (
    WRAPPED_KEY_SCHEMA,
    generate_wrapped_key_file,
)
from synapse_channel.core.at_rest_pkcs11 import (
    DEFAULT_KEK_LABEL,
    PKCS11_BACKEND,
    cipher_from_wrapped_key_file_pkcs11,
    generate_wrapped_key_file_pkcs11,
)

_SOFTHSM_MODULE_PATHS = (
    "/usr/lib/softhsm/libsofthsm2.so",
    "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
    "/usr/local/lib/softhsm/libsofthsm2.so",
    "/opt/homebrew/lib/softhsm/libsofthsm2.so",
)


def _softhsm_module() -> str | None:
    """Return the first SoftHSM2 PKCS#11 module found on disk, or ``None``."""
    for path in _SOFTHSM_MODULE_PATHS:
        if Path(path).exists():
            return path
    return None


def _pkcs11_available() -> bool:
    """Return whether python-pkcs11, a SoftHSM2 module, and softhsm2-util are all present."""
    try:
        import pkcs11  # noqa: F401
    except ImportError:
        return False
    return _softhsm_module() is not None and shutil.which("softhsm2-util") is not None


# The backend needs a live PKCS#11 token. CI installs python-pkcs11 + softhsm2 so these run and
# count toward coverage; elsewhere they skip gracefully rather than erroring on a missing token.
pytestmark = pytest.mark.skipif(
    not _pkcs11_available(),
    reason="requires python-pkcs11 + softhsm2 (installed in CI)",
)


@pytest.fixture(scope="session")
def softhsm_token(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    """Initialise one isolated SoftHSM2 token for the session and yield its connection info.

    The token is session-scoped because ``pkcs11.lib()`` calls ``C_Initialize`` once per process and
    caches the ``SOFTHSM2_CONF`` it reads then, so the config must be fixed before the first token
    operation and stay constant for the run.
    """
    module = _softhsm_module()
    assert module is not None  # guaranteed by the skipif above
    token_dir = tmp_path_factory.mktemp("softhsm-tokens")
    conf = tmp_path_factory.mktemp("softhsm-conf") / "softhsm2.conf"
    conf.write_text(
        f"directories.tokendir = {token_dir}\nobjectstore.backend = file\nlog.level = ERROR\n",
        encoding="utf-8",
    )
    previous = os.environ.get("SOFTHSM2_CONF")
    os.environ["SOFTHSM2_CONF"] = str(conf)
    subprocess.run(  # noqa: S603 - fixed argv, no shell, test-only token setup
        [
            "softhsm2-util",
            "--init-token",
            "--slot",
            "0",
            "--label",
            "synapse-test",
            "--pin",
            "1234",
            "--so-pin",
            "5678",
        ],
        check=True,
        capture_output=True,
    )
    try:
        yield {"module_path": module, "token_label": "synapse-test", "pin": "1234"}
    finally:
        if previous is None:
            os.environ.pop("SOFTHSM2_CONF", None)
        else:
            os.environ["SOFTHSM2_CONF"] = previous


def _generate(token: dict[str, str], path: Path, **kwargs: object) -> Path:
    return generate_wrapped_key_file_pkcs11(
        path,
        module_path=token["module_path"],
        token_label=token["token_label"],
        pin=token["pin"],
        **kwargs,  # type: ignore[arg-type]
    )


def test_generate_wraps_on_token_and_round_trips(
    softhsm_token: dict[str, str], tmp_path: Path
) -> None:
    key_path = tmp_path / "hw.wrapped.key"
    _generate(softhsm_token, key_path)
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"
    document = json.loads(key_path.read_text(encoding="utf-8"))
    assert document["schema"] == WRAPPED_KEY_SCHEMA
    assert document["backend"] == PKCS11_BACKEND
    assert document["params"] == {"token_label": "synapse-test", "key_label": DEFAULT_KEK_LABEL}

    cipher = cipher_from_wrapped_key_file_pkcs11(
        key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
    )
    blob = cipher.encrypt(b"hardware-wrapped secret")
    # A second, independent load unwraps the same data key on the token, so the blob still decrypts.
    reopened = cipher_from_wrapped_key_file_pkcs11(
        key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
    )
    assert reopened.decrypt(blob) == b"hardware-wrapped secret"


def test_generate_reuses_an_existing_token_key(
    softhsm_token: dict[str, str], tmp_path: Path
) -> None:
    # The default key-encryption key is created on first use and reused after, so two files wrap
    # under the same token key and both load.
    first = _generate(softhsm_token, tmp_path / "a.key")
    second = _generate(softhsm_token, tmp_path / "b.key")
    for key_path in (first, second):
        cipher = cipher_from_wrapped_key_file_pkcs11(
            key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
        )
        assert cipher.decrypt(cipher.encrypt(b"x")) == b"x"


def test_generate_refuses_to_overwrite(softhsm_token: dict[str, str], tmp_path: Path) -> None:
    key_path = tmp_path / "hw.wrapped.key"
    _generate(softhsm_token, key_path)
    with pytest.raises(FileExistsError):
        _generate(softhsm_token, key_path)


def test_generate_without_creating_an_absent_key_errors(
    softhsm_token: dict[str, str], tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="no PKCS#11 key-encryption key labelled 'absent-kek'"):
        _generate(softhsm_token, tmp_path / "hw.key", key_label="absent-kek", create_kek=False)


def test_load_rejects_a_non_pkcs11_file(softhsm_token: dict[str, str], tmp_path: Path) -> None:
    passphrase_file = tmp_path / "pp.wrapped.key"
    generate_wrapped_key_file(passphrase_file, "pw", n=2**10)
    with pytest.raises(ValueError, match="not PKCS#11"):
        cipher_from_wrapped_key_file_pkcs11(
            passphrase_file, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
        )


def test_load_rejects_a_missing_token_key(softhsm_token: dict[str, str], tmp_path: Path) -> None:
    key_path = tmp_path / "hw.wrapped.key"
    _generate(softhsm_token, key_path)
    # Point the file at a key label the token does not hold.
    document = json.loads(key_path.read_text(encoding="utf-8"))
    document["params"]["key_label"] = "never-generated"
    key_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="key labelled 'never-generated'"):
        cipher_from_wrapped_key_file_pkcs11(
            key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
        )


def test_load_rejects_malformed_pkcs11_params(
    softhsm_token: dict[str, str], tmp_path: Path
) -> None:
    key_path = tmp_path / "malformed.key"
    key_path.write_text(
        json.dumps(
            {
                "schema": WRAPPED_KEY_SCHEMA,
                "backend": PKCS11_BACKEND,
                "params": {"token_label": "synapse-test"},  # no key_label
                "wrapped_key": "AAAA",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed PKCS#11 wrapped at-rest key file"):
        cipher_from_wrapped_key_file_pkcs11(
            key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
        )


def _pkcs11_cli_args(token: dict[str, str], path: Path, *extra: str) -> list[str]:
    return [
        "encrypt-key",
        "generate-wrapped-pkcs11",
        "--pkcs11-module",
        token["module_path"],
        "--token-label",
        token["token_label"],
        *extra,
        str(path),
    ]


def test_cli_generate_wrapped_pkcs11_round_trips_and_refuses_overwrite(
    softhsm_token: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from synapse_channel import cli, cli_encrypt_key

    key_path = tmp_path / "cli.wrapped.key"
    # The PIN comes from the environment here, so the interactive reader must not be consulted.
    monkeypatch.setenv("PKCS11_PIN", softhsm_token["pin"])

    def _no_prompt(_prompt: str) -> str:
        raise AssertionError("PIN prompt should not be used when PKCS11_PIN is set")

    args = cli.build_parser().parse_args(_pkcs11_cli_args(softhsm_token, key_path))
    assert cli_encrypt_key._cmd_generate_wrapped_pkcs11(args, pin_reader=_no_prompt) == 0
    assert "PKCS#11-wrapped at-rest key" in capsys.readouterr().out

    cipher = cipher_from_wrapped_key_file_pkcs11(
        key_path, module_path=softhsm_token["module_path"], pin=softhsm_token["pin"]
    )
    assert cipher.decrypt(cipher.encrypt(b"z")) == b"z"

    again = cli.build_parser().parse_args(_pkcs11_cli_args(softhsm_token, key_path))
    assert cli_encrypt_key._cmd_generate_wrapped_pkcs11(again, pin_reader=_no_prompt) == 1
    assert "refusing to overwrite" in capsys.readouterr().out


def test_cli_generate_wrapped_pkcs11_no_create_kek_errors(
    softhsm_token: dict[str, str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from synapse_channel import cli, cli_encrypt_key

    monkeypatch.delenv("PKCS11_PIN", raising=False)
    args = cli.build_parser().parse_args(
        _pkcs11_cli_args(
            softhsm_token, tmp_path / "w.key", "--key-label", "cli-absent-kek", "--no-create-kek"
        )
    )
    rc = cli_encrypt_key._cmd_generate_wrapped_pkcs11(
        args, pin_reader=lambda _p: softhsm_token["pin"]
    )
    assert rc == 2
    assert "no PKCS#11 key-encryption key" in capsys.readouterr().out
