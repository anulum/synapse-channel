# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — actual instrumented bootstrap lifecycle
"""Exercise the pinned command through real private-namespace refusal and publication."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest
import websockets
from tools.protected_runtime_probe.prepare import prepare_packet

from fixtures.protected_runtime_image import (
    materialize_instrumented_packet,
    samsung_backing_or_refusal,
)

_SOURCE = Path(__file__).resolve().parents[1]
_WORKFLOW = """import json,os,shlex,subprocess,sys
from pathlib import Path
packet=Path(sys.argv[1]);mode=sys.argv[2]
review=json.loads((packet/'operator-review.json').read_bytes())
command=shlex.split(review['operator_command'])
assert command[:4]==['/usr/bin/python3.12','-I','-B','-S']
assert '/usr/bin/sudo' not in command
assert command[4]=='-c'
runs=Path(json.loads((packet/'manifest.json').read_bytes())['backing_parent'])
before=sorted((p.name,p.stat().st_ino)for p in runs.iterdir())if runs.exists()else None
if mode=='missing-selector':assert command.pop()=='--instrumentation'
elif mode=='unexpected-selector':command.append('--instrumentation')
assert os.getresuid()==(0,0,0)and os.getresgid()==(0,0,0)
process=subprocess.run(command,stdin=subprocess.DEVNULL,capture_output=True,text=True,
 timeout=90,check=False,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
assert process.returncode!=0 and not process.stdout
expected=('initial user namespace' if mode=='instrumented'
 else 'root instrumentation selector differs')
assert expected in process.stderr,process.stderr
after=sorted((p.name,p.stat().st_ino)for p in runs.iterdir())if runs.exists()else None
assert after==before
assert not Path('/opt/synapse-channel').exists()
assert not Path('/etc/passwd').exists()
assert 'create_backing' not in process.stderr
print(json.dumps({'refused':True,'mode':mode,'host_root_qualified':False,'error':expected}))
"""


def _artifact(directory: Path, name: str) -> Path:
    """Acquire one complete original input and require its exact production source pin."""
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )[name]
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL") if name == "coverage" else None
    path = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            path.write_bytes(response.read(8 * 1024 * 1024 + 1))
    raw = path.read_bytes()
    assert len(raw) == pin["size_bytes"] and hashlib.sha256(raw).hexdigest() == pin["sha256"]
    return path


@pytest.fixture(scope="module")
def packets(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path] | None:
    """Prepare real source/native/wheel-bound input without invoking any root entry."""
    directory = tmp_path_factory.mktemp("instrumented-bootstrap-packets")
    if not samsung_backing_or_refusal(directory):
        return None
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        with pytest.raises(OSError, match="native Linux"):
            prepare_packet(_SOURCE, directory / "unsupported", directory / "runs")
        return None
    collector = _artifact(directory, "coverage")
    dependency = _artifact(directory, "websockets")
    instrumented, _review = materialize_instrumented_packet(
        _SOURCE, Path(websockets.__file__).parent, directory, collector, dependency
    )
    production = directory / "production-packet"
    prepare_packet(_SOURCE, production, directory / "unused-production-runs")
    return instrumented, production


def _execute(packet: Path, directory: Path, mode: str) -> subprocess.CompletedProcess[str]:
    """Execute the exact reviewed command as actual mapped root, with no host sudo."""
    command = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-user",
        "--uid",
        "0",
        "--gid",
        "0",
        "--unshare-net",
        "--unshare-pid",
        "--tmpfs",
        "/",
    ]
    for root in ("/usr", "/media", "/home", "/lib", "/lib64"):
        command += ["--ro-bind", root, root]
    command += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--bind",
        str(directory),
        str(directory),
        "--cap-add",
        "ALL",
        "/usr/bin/python3.12",
        "-I",
        "-B",
        "-S",
        "-c",
        _WORKFLOW,
        str(packet),
        mode,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=120,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )


@pytest.mark.parametrize("mode", ["instrumented", "missing-selector", "unexpected-selector"])
def test_actual_pinned_command_refuses_mapped_root_and_wrong_selector(
    packets: tuple[Path, Path] | None, mode: str
) -> None:
    """The real pinned command refuses wrong profiles and mapped root before setup."""
    if packets is None:
        return
    assert os.geteuid() != 0
    packet = packets[1] if mode == "unexpected-selector" else packets[0]
    result = _execute(packet, packet.parent, mode)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["host_root_qualified"] is False
    assert observed["refused"] is True
    assert observed["error"] == (
        "initial user namespace"
        if mode == "instrumented"
        else "root instrumentation selector differs"
    )
