# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — native role and account-free credential boundary fixture
"""Exercise actual NSS mounts and kernel refusals without host root authority.

The outer user namespace maps only the test runner's UID/GID. Its numeric
credential transition to a different unmapped role must refuse. This verifies
the account-free boundary, not a successful dedicated host credential drop or
the complete hash-pinned production runtime image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

_ENTRY_OBSERVER = r"""import hashlib,json,runpy,sys
from pathlib import Path
fixture,package,output=map(Path,sys.argv[1:4])
sys.path.insert(0,str(package.parent));import coverage
sys.argv=[str(fixture),*sys.argv[4:]]
recorder=coverage.Coverage(data_file=str(output/'.coverage'),data_suffix=True,
 branch=True,include=[str(fixture)],config_file=False)
recorder.start();recorder.switch_context('measured-diagnostic/role-boundary-entry')
try:runpy.run_path(str(fixture),run_name='__main__')
finally:
 recorder.stop();recorder.save()
 data_path=Path(recorder.get_data().data_filename())
 binding=output/('source-binding-'+data_path.name+'.json')
 with binding.open('x',encoding='ascii') as stream:
  json.dump({'data_filename':data_path.name,'sources':[{'module':'__main__',
   'path':str(fixture.resolve(strict=True)),
   'sha256':hashlib.sha256(fixture.read_bytes()).hexdigest()}]},stream)
  stream.write('\n')
"""


def measured_entry_command(
    interpreter: list[str],
    fixture: Path,
    arguments: list[str],
    directory: Path,
    coverage_package: Path,
) -> list[str]:
    """Measure the complete real fixture entry with the original isolation flags.

    Parameters
    ----------
    interpreter:
        Actual interpreter or loader command, including its original flags.
    fixture:
        Real fixture path visible in this process's mount namespace.
    arguments:
        Unchanged parent or child CLI arguments for the credential boundary.
    directory:
        Existing writable measurement directory in this mount namespace.
    coverage_package:
        Actual read-only Coverage.py package made visible to this process.

    Returns
    -------
    list[str]
        Command measuring the real CLI entry and writing source-bound data.
    """
    return [
        *interpreter,
        "-c",
        _ENTRY_OBSERVER,
        str(fixture),
        str(coverage_package),
        str(directory),
        *arguments,
    ]


@contextmanager
def trace(arguments: argparse.Namespace) -> Iterator[None]:
    """Optionally record standard Coverage.py data inside the owned test projection."""
    if arguments.coverage_dir is None:
        yield
        return
    sys.path.insert(0, str(arguments.coverage_package.parent))
    import coverage

    recorder = coverage.Coverage(
        data_file=str(arguments.coverage_dir / ".coverage"),
        data_suffix=True,
        branch=True,
        include=[str(Path(__file__).resolve()), "*/tools/protected_runtime_probe/*.py"],
        config_file=False,
        context="measured-diagnostic/role-boundary-operation",
    )
    recorder.start()
    try:
        yield
    finally:
        recorder.stop()
        recorder.save()
        data_path = Path(recorder.get_data().data_filename())
        fixture = Path(__file__).resolve(strict=True)
        sources = []
        for filename in sorted(recorder.get_data().measured_files()):
            path = Path(filename).resolve(strict=True)
            module = "__main__" if path == fixture else "tools.protected_runtime_probe"
            if path != fixture and path.name != "__init__.py":
                module += "." + path.stem
            sources.append(
                {
                    "module": module,
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        binding = arguments.coverage_dir / ("source-binding-" + data_path.name + ".json")
        with binding.open("x", encoding="ascii") as stream:
            json.dump({"data_filename": data_path.name, "sources": sources}, stream)
            stream.write("\n")


def child(arguments: argparse.Namespace) -> dict[str, Any]:
    """Call the actual public credential API under the selected real filesystem."""
    sys.path.insert(0, "/probe")
    from tools.protected_runtime_probe.roles import Role, drop_enrolled_identity, drop_privileges

    identity = json.loads(arguments.identity)
    before = os.getresuid()
    try:
        if arguments.method == "numeric":
            drop_enrolled_identity(identity["uid"], identity["gid"])
        else:
            drop_privileges(Role("synapse-writer", identity["uid"], identity["gid"]))
    except (OSError, ValueError) as error:
        return {
            "error": type(error).__name__,
            "errno": getattr(error, "errno", None),
            "message": str(error),
            "uids_before": before,
            "uids_after": os.getresuid(),
            "passwd_present": Path("/etc/passwd").exists(),
            "host_usr_absent": not Path("/usr").exists(),
        }
    raise RuntimeError("a single-UID namespace admitted a distinct unmapped identity")


def parent(arguments: argparse.Namespace) -> dict[str, Any]:
    """Validate real private account records before launching the account-free child."""
    sys.path.insert(0, str(arguments.probe.parent))
    from tools.protected_runtime_probe.roles import (
        NAMES,
        Role,
        resolve_role_identity,
        validate_role_identity,
    )

    before_modules = set(sys.modules)
    try:
        resolved = [resolve_role_identity(name) for name in NAMES]
        role = validate_role_identity(Role("synapse-writer", 902, 902))
    except ValueError as error:
        return {"phase": "parent_refused", "error": type(error).__name__, "message": str(error)}
    identity = arguments.identity or json.dumps({"uid": role.uid, "gid": role.gid})
    command = [
        arguments.bwrap,
        "--unshare-pid",
        "--ro-bind",
        str(arguments.prefix),
        "/runtime/python",
        "--ro-bind",
        str(arguments.loader),
        "/runtime/loader",
        "--ro-bind",
        str(arguments.loader.parent),
        "/runtime/systemlib",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/etc",
        "--ro-bind",
        str(arguments.probe),
        "/probe/tools",
        "--ro-bind",
        str(Path(__file__).resolve()),
        "/boundary.py",
        "--cap-drop",
        "ALL",
    ]
    for capability in ("CAP_SETPCAP", "CAP_SETUID", "CAP_SETGID"):
        command.extend(("--cap-add", capability))
    if arguments.with_nss:
        command.extend(("--ro-bind", str(arguments.account_files), "/etc"))
    if arguments.coverage_dir is not None:
        command.extend(
            (
                "--bind",
                str(arguments.coverage_dir),
                "/coverage",
                "--ro-bind",
                str(arguments.coverage_package),
                "/coverage-library/coverage",
            )
        )
    interpreter = [
        "/runtime/loader",
        "--inhibit-cache",
        "--library-path",
        "/runtime/python/lib:/runtime/systemlib",
        "/runtime/python/" + arguments.executable,
        "-I",
        "-B",
    ]
    entry_arguments = [
        "child",
        "--method",
        arguments.method,
        "--identity",
        identity,
    ]
    if arguments.coverage_dir is not None:
        entry_arguments.extend(
            ("--coverage-dir", "/coverage", "--coverage-package", "/coverage-library/coverage")
        )
        command.extend(
            measured_entry_command(
                interpreter,
                Path("/boundary.py"),
                entry_arguments,
                Path("/coverage"),
                Path("/coverage-library/coverage"),
            )
        )
    else:
        command.extend((*interpreter, "/boundary.py", *entry_arguments))
    result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError("native credential fixture failed: " + result.stderr)
    return {
        "phase": "child_refused",
        "parent_role": asdict(role),
        "resolved_local_roles": [asdict(item) for item in resolved],
        "account_backend_modules_loaded": sorted(
            (set(sys.modules) - before_modules) & {"pwd", "grp", "spwd"}
        ),
        "child": json.loads(result.stdout),
    }


def main() -> None:
    """Print one bounded native result from either side of the account boundary."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("parent", "child"))
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--account-files", type=Path)
    parser.add_argument("--prefix", type=Path)
    parser.add_argument("--loader", type=Path)
    parser.add_argument("--executable")
    parser.add_argument("--bwrap", default="/usr/bin/bwrap")
    parser.add_argument("--method", choices=("numeric", "named"), default="numeric")
    parser.add_argument("--identity")
    parser.add_argument("--with-nss", action="store_true")
    parser.add_argument("--coverage-dir", type=Path)
    parser.add_argument("--coverage-package", type=Path)
    arguments = parser.parse_args()

    def invoke() -> None:
        """Print the actual CLI result from a fresh frame inside the collector context."""
        result = parent(arguments) if arguments.mode == "parent" else child(arguments)
        print(json.dumps(result, sort_keys=True))

    trace(arguments)(invoke)()


if __name__ == "__main__":
    main()


def run_mapped_public_refusal(
    source: Path,
    directory: Path,
    entry: str,
    *,
    account_case: str | None = None,
    cgroup_directory: Path | None = None,
    drop_capabilities: bool = False,
) -> dict[str, Any]:
    """Run a public refusal in a real single-UID namespace and compare its observation.

    Parameters
    ----------
    source:
        Checkout containing the original public probe code.
    directory:
        Owned scratch directory for invalid account inputs and observation receipts.
    entry:
        Fixed test program calling only public production entry points.
    account_case:
        Optional unsafe local passwd input; it is never installed on the host.
    cgroup_directory:
        Optional real kernel directory bound read-only inside the private namespace.
    drop_capabilities:
        Remove the child's capabilities before its public credential API call.

    Returns
    -------
    dict[str, Any]
        The actual refusal receipt, equal in the cold and observed executions.
    """
    import coverage

    from fixtures.capable_bwrap import capable_bwrap

    accounts = directory / "account-inputs"
    accounts.mkdir(mode=0o700)
    passwd = b"synapse-writer:x:900:900:fixture:/nonexistent:/usr/sbin/nologin\n"
    if account_case == "bytebound":
        passwd = b"#" * (4 * 1024 * 1024 + 1)
    elif account_case == "malformed":
        passwd = b"synapse-writer:x:900\n"
    elif account_case == "duplicate":
        passwd *= 2
    for name, raw in {
        "passwd": passwd,
        "group": b"synapse-writer:x:900:\n",
        "shadow": b"synapse-writer:!:0:0:99999:7:::\n",
        "login.defs": b"UID_MIN 1000\nGID_MIN 1000\n",
    }.items():
        path = accounts / name
        path.write_bytes(raw)
        path.chmod(0o666 if name == "passwd" and account_case == "mode" else 0o600)
    pins = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in accounts.iterdir()}
    programme = """import hashlib,json,os,sys
from pathlib import Path
source,site,observed,entry=sys.argv[1:]
sys.path[:0]=[source,source+'/src',site]
recorder=None
if observed=='yes':
 import coverage
 recorder=coverage.Coverage(data_file=None,branch=True,config_file=False,
  include=[source+'/tools/protected_runtime_probe/*.py'])
 recorder.start();recorder.switch_context('measured-diagnostic/public-namespace-refusal')
try:
 assert os.getresuid()==os.getresgid()==(0,0,0)
 mapping=Path('/proc/self/uid_map').read_text().split()
 assert len(mapping)==3 and mapping[0]=='0' and int(mapping[1])>0 and mapping[2]=='1'
 before=len(os.listdir('/proc/self/fd'))
 exec(entry)
 assert len(os.listdir('/proc/self/fd'))==before
 assert os.getresuid()==os.getresgid()==(0,0,0)
finally:
 if recorder is not None:
  recorder.stop();data=recorder.get_data()
  print('SYNAPSE_PUBLIC_REFUSAL_OBSERVATION='+json.dumps({
   'data_hex':data.dumps().hex(),
   'source_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest()
    for name in data.measured_files()}}),file=sys.stderr)
"""
    rows: list[subprocess.CompletedProcess[str]] = []
    for observed in (False, True):
        command = [
            capable_bwrap(),
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--uid",
            "0",
            "--gid",
            "0",
            "--unshare-net",
            "--ro-bind",
            "/",
            "/",
            "--proc",
            "/proc",
            "--ro-bind",
            str(accounts),
            "/etc",
            "--chdir",
            "/",
        ]
        if cgroup_directory is not None:
            command.extend(("--ro-bind", str(cgroup_directory), "/sys/fs/cgroup"))
        command.extend(("--cap-drop", "ALL") if drop_capabilities else ("--cap-add", "ALL"))
        command.extend(
            (
                sys.executable,
                "-I",
                "-B",
                "-S",
                "-W",
                "error::DeprecationWarning",
                "-W",
                "error::ResourceWarning",
                "-c",
                programme,
                str(source),
                str(Path(coverage.__file__).parent.parent),
                "yes" if observed else "no",
                entry,
            )
        )
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": "/nonexistent"},
        )
        if observed:
            stderr, marker, transport = result.stderr.rpartition(
                "SYNAPSE_PUBLIC_REFUSAL_OBSERVATION="
            )
            assert marker, result.stderr
            binding = json.loads(transport)
            for name, pin in binding["source_sha256"].items():
                path = Path(name).resolve(strict=True)
                assert path.is_relative_to(source / "tools/protected_runtime_probe")
                assert hashlib.sha256(path.read_bytes()).hexdigest() == pin
            data = coverage.CoverageData(
                basename=str(directory / "public-refusal-observations.coverage")
            )
            try:
                data.loads(bytes.fromhex(binding["data_hex"]))
                assert set(data.measured_files()) == set(binding["source_sha256"])
                assert data.measured_contexts() == {"measured-diagnostic/public-namespace-refusal"}
                active = coverage.Coverage.current()
                if active is not None:
                    active.get_data().update(data)
                output = os.environ.get("SYNAPSE_TEST_COVERAGE_DIR")
                if output:
                    saved = coverage.CoverageData(
                        basename=str(Path(output) / ".coverage"), suffix=True
                    )
                    try:
                        saved.update(data)
                        saved.write()
                    finally:
                        saved.close(force=True)
            finally:
                data.close(force=True)
            (directory / "public-refusal-observation.json").write_text(
                json.dumps(binding, indent=2) + "\n"
            )
            result = subprocess.CompletedProcess(
                result.args, result.returncode, result.stdout, stderr
            )
        assert result.returncode == 0, result.stderr
        rows.append(result)
    assert rows[0].stdout == rows[1].stdout and rows[0].stderr == rows[1].stderr
    assert pins == {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in pins}
    value: dict[str, Any] = json.loads(rows[0].stdout)
    assert value["refused"] is True
    return value
