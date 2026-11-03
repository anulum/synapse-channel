# SPDX-License-Identifier: AGPL-3.0-or-later
# Commercial license available
# © Concepts 1996–2026 Miroslav Šotek. All rights reserved.
# © Code 2020–2026 Miroslav Šotek. All rights reserved.
# ORCID: 0009-0009-3560-0851
# Contact: www.anulum.li | protoscience@anulum.li
# SYNAPSE CHANNEL — fixed instrumentation child entry contracts
"""Exercise the real fixed entry under the ordinary system interpreter, without UID drops."""

from __future__ import annotations

import ast
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
from tools.protected_runtime_probe.instrumentation_launch import RecordingLaunch
from tools.protected_runtime_probe.instrumentation_recording import prepare_recording
from tools.protected_runtime_probe.runtime import probe_python_command

from fixtures.protected_runtime_image import (
    materialize_instrumentation_view,
    samsung_backing_or_refusal,
)

_SOURCE = Path(__file__).resolve().parents[1]
_READ_DATA = """import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import coverage
assert coverage.__version__=='7.17.0'
data=coverage.CoverageData(basename=sys.argv[2]);data.read()
try:
 files=sorted(data.measured_files())
 print(json.dumps({'contexts':sorted(data.measured_contexts()),
  'arcs':{name:len(data.arcs(name) or []) for name in files}}))
finally: data.close(force=True)
"""

_MEASURED_ENTRY = """import hashlib,json,runpy,sys,traceback
from pathlib import Path
settings=json.loads(sys.argv[1]);entry=sys.argv[2];sys.argv=['-c',*sys.argv[3:]]
site=Path(settings['view'])/'runtime/python/lib/python3.12/dist-packages'
sys.path.insert(0,str(site));import coverage
assert coverage.__version__=='7.17.0'
assert Path(coverage.__file__).is_relative_to(site/'coverage')
files=sorted((Path(settings['view'])/'probe/protected_runtime_probe').glob('*.py'))
pins={str(path):hashlib.sha256(path.read_bytes()).hexdigest()for path in files}
collector=coverage.Coverage(data_file=None,branch=True,config_file=False,include=sorted(pins))
collector.set_option('run:core','ctrace')
code=0;collector.start();collector.switch_context('measured-diagnostic/fixed-entry')
try:
 try:exec(entry)
 except SystemExit as error:
  if error.code is None:code=0
  elif isinstance(error.code,int):code=int(error.code)&255
  else:print(error.code,file=sys.stderr);code=1
 except BaseException:
  traceback.print_exc();code=1
finally:collector.stop()
assert pins=={str(path):hashlib.sha256(path.read_bytes()).hexdigest()for path in files}
data=collector.get_data()
record={'profile':'measured-diagnostic/fixed-entry','original_exit':code,
 'coverage_version':coverage.__version__,'data_hex':data.dumps().hex(),
 'source_sha256':{name:pins[name]for name in data.measured_files()},
 'first_import_qualification':False,'isolation_qualification':False,
 'flag_qualification':False,'native_qualification':False,'whole_scope_qualification':False}
raw=json.dumps(record,sort_keys=True);assert len(raw.encode('utf-8'))<=2*1024*1024
print('\\nSYNAPSE_ENTRY_OBSERVATION='+raw,file=sys.stderr)
raise SystemExit(code)
"""


@pytest.fixture(scope="module")
def launch_view(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str] | None:
    """Prepare exact actual code and original collector as ordinary fixture input."""
    if (sys.platform, platform.machine(), sys.version_info[:2]) != ("linux", "x86_64", (3, 12)):
        with pytest.raises(OSError, match="native Linux"):
            prepare_recording(
                Path("unopened"), "0" * 64, role="client", output_descriptor=-1, uid=0, gid=0
            )
        return None
    directory = tmp_path_factory.mktemp("instrumentation-launch-view")
    if not samsung_backing_or_refusal(directory):
        return None
    supplied = os.environ.get("SYNAPSE_INSTRUMENTATION_WHEEL")
    pin = json.loads(
        (_SOURCE / "tools/protected_runtime_probe/native-dependencies.lock.json").read_bytes()
    )["coverage"]
    artifact = Path(supplied) if supplied else directory / pin["filename"]
    if not supplied:
        with urllib.request.urlopen(pin["source_url"], timeout=20) as response:
            artifact.write_bytes(response.read(8 * 1024 * 1024 + 1))
    return materialize_instrumentation_view(
        _SOURCE, Path(websockets.__file__).parent, directory, artifact
    )


def _execute_entry(
    view: Path,
    digest: str,
    leaf: Path,
    *,
    role: str = "client",
    module: str = "authority_child",
    arguments: tuple[str, ...] = ("--help",),
    small_file_limit: bool = False,
    measured: bool = False,
    namespace_root: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run the actual generated entry, leaving loader/root qualification explicitly separate."""
    descriptor = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    read, write = os.pipe()
    canary = b"original construction channel is not recorder enrollment\n"
    try:
        os.write(write, canary)
        os.close(write)
        write = -1
        enrollment = RecordingLaunch(
            role,
            digest,
            0 if namespace_root else os.geteuid(),
            0 if namespace_root else os.getegid(),
            None if role == "writer" else descriptor,
        )
        command = probe_python_command(view, module, captured_runtime=True, recording=enrollment)
        child = ["/usr/bin/python3.12", "-I", "-B", "-S", "-c", command[-1], *arguments]
        if measured:
            child = [
                "/usr/bin/python3.12",
                "-I",
                "-B",
                "-S",
                "-c",
                _MEASURED_ENTRY,
                json.dumps({"view": str(view)}),
                command[-1],
                *arguments,
            ]
        if namespace_root:
            namespace = [
                "/usr/bin/bwrap",
                "--die-with-parent",
                "--new-session",
                "--unshare-user",
                "--uid",
                "0",
                "--gid",
                "0",
                "--unshare-net",
                "--tmpfs",
                "/",
            ]
            for directory in ("/usr", "/lib", "/lib64"):
                namespace += ["--ro-bind", directory, directory]
            child = [
                *namespace,
                "--ro-bind",
                str(view),
                str(view),
                "--proc",
                "/proc",
                "--chdir",
                "/",
                *child,
            ]

        def set_file_limit() -> None:
            import resource

            resource.setrlimit(resource.RLIMIT_FSIZE, (4096, 4096))

        result = subprocess.run(
            child,
            pass_fds=(descriptor,),
            stdin=read,
            capture_output=True,
            text=True,
            timeout=60,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            preexec_fn=set_file_limit if small_file_limit else None,
            check=False,
        )
        assert os.read(read, len(canary) + 1) == canary
        if measured:
            result = _retain_entry_measurement(view, leaf, result)
        elif os.environ.get("SYNAPSE_TEST_ENTRY_MEASUREMENT") == "1":
            paired_leaf = leaf.parent / ("paired-" + leaf.name)
            paired_leaf.mkdir(mode=0o700)
            paired = _execute_entry(
                view,
                digest,
                paired_leaf,
                role=role,
                module=module,
                arguments=arguments,
                small_file_limit=small_file_limit,
                measured=True,
                namespace_root=namespace_root,
            )
            assert (paired.returncode, paired.stdout) == (result.returncode, result.stdout)
            cold_error = result.stderr.strip().splitlines()
            measured_error = paired.stderr.strip().splitlines()
            assert (cold_error[-1] if cold_error else "") == (
                measured_error[-1] if measured_error else ""
            ), (result.stderr, paired.stderr)
            evidence = paired_leaf.parent / (paired_leaf.name + "-entry-observation.json")
            pairing = {
                "profile": "cold-and-measured-fixed-entry-pair",
                "role": role,
                "module": module,
                "arguments": arguments,
                "inherited_file_limit_bytes": 4096 if small_file_limit else None,
                "mapped_single_uid_namespace": namespace_root,
                "cold_original": {
                    "exit": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                "measured": {
                    "exit": paired.returncode,
                    "stdout": paired.stdout,
                    "stderr": paired.stderr,
                },
                "observation_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                "native_qualification": False,
                "whole_scope_qualification": False,
            }
            with (leaf.parent / (leaf.name + "-cold-pair.json")).open(
                "x", encoding="utf-8"
            ) as stream:
                json.dump(pairing, stream, sort_keys=True)
        return result
    finally:
        os.close(descriptor)
        os.close(read)
        if write >= 0:
            os.close(write)


def _retain_entry_measurement(
    view: Path, leaf: Path, result: subprocess.CompletedProcess[str]
) -> subprocess.CompletedProcess[str]:
    """Retain labelled real entry arcs after exact current-source and origin checks."""
    import coverage

    product_stderr, marker, raw = result.stderr.rpartition("\nSYNAPSE_ENTRY_OBSERVATION=")
    assert marker and 0 < len(raw.encode("utf-8")) <= 2 * 1024 * 1024
    value = json.loads(raw)
    assert value["profile"] == "measured-diagnostic/fixed-entry"
    assert value["coverage_version"] == coverage.__version__
    assert value["original_exit"] == result.returncode
    assert value["first_import_qualification"] is False
    assert value["isolation_qualification"] is False
    assert value["flag_qualification"] is False
    assert value["native_qualification"] is False
    assert value["whole_scope_qualification"] is False
    data = coverage.CoverageData(no_disk=True)
    data.loads(bytes.fromhex(value["data_hex"]))
    assert data.has_arcs() and data.measured_files()
    assert data.measured_contexts() == {"measured-diagnostic/fixed-entry"}
    assert set(data.measured_files()) == set(value["source_sha256"])
    mapping: dict[str, str] = {}
    for name, pin in value["source_sha256"].items():
        relative = Path(name).relative_to(view / "probe/protected_runtime_probe")
        target = _SOURCE / "tools/protected_runtime_probe" / relative
        assert pin == hashlib.sha256(Path(name).read_bytes()).hexdigest()
        assert pin == hashlib.sha256(target.read_bytes()).hexdigest()
        mapping[name] = str(target)
    mapped = coverage.CoverageData(no_disk=True)
    mapped.update(data, map_path=mapping.__getitem__)
    active = coverage.Coverage.current()
    assert active is not None, "entry diagnostic requires the actual parent branch collector"
    assert active.get_option("run:branch") is True
    active.get_data().update(mapped)
    value["cold_qualification"] = False
    value["mapped_source_names"] = mapping
    evidence = leaf.parent / (leaf.name + "-entry-observation.json")
    with evidence.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
    return subprocess.CompletedProcess(
        result.args, result.returncode, result.stdout, product_stderr
    )


def _read_dataset(view: Path, leaf: Path, expected_source: str, role: str) -> None:
    """Use the pinned actual collector only as the ordinary fixture owner."""
    result = subprocess.run(
        [
            "/usr/bin/python3.12",
            "-I",
            "-B",
            "-S",
            "-c",
            _READ_DATA,
            str(view / "runtime/python/lib/python3.12/dist-packages"),
            str(leaf / "coverage.sqlite"),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["contexts"] == [role]
    source = str(view / "probe/protected_runtime_probe" / expected_source)
    assert data["arcs"][source] > 0
    assert set(os.listdir(leaf)) == {"coverage.sqlite"}


@pytest.mark.parametrize("role", ["authority", "client", "operator"])
def test_actual_fixed_entry_collects_module_import_and_preserves_successful_exit(
    launch_view: tuple[Path, str] | None, tmp_path: Path, role: str
) -> None:
    """The real public help entry records its import and argparse exit, with stdin intact."""
    if launch_view is None:
        return
    view, digest = launch_view
    result = _execute_entry(view, digest, tmp_path, role=role)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage:")
    _read_dataset(view, tmp_path, "authority_child.py", role)


def test_actual_target_argument_failure_preserves_failure_and_records_its_interval(
    launch_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """A real target parser failure exits2, saves its measured interval and leaves stdin intact."""
    if launch_view is None:
        return
    view, digest = launch_view
    result = _execute_entry(view, digest, tmp_path, arguments=("--unrecognised-option",))
    assert result.returncode == 2
    assert "--cgroup" in result.stderr
    _read_dataset(view, tmp_path, "authority_child.py", "client")


def test_actual_namespace_root_refusal_is_saved_without_weakening_the_target(
    launch_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """An ordinary wrapper records its actual root-context refusal without changing the target."""
    if launch_view is None:
        return
    view, digest = launch_view
    result = _execute_entry(
        view, digest, tmp_path, role="namespace-wrapper", module="namespace_child", arguments=()
    )
    assert result.returncode == 1
    assert "requires the root controller" in result.stderr
    _read_dataset(view, tmp_path, "namespace_child.py", "namespace-wrapper")


def test_actual_save_failure_overrides_a_provisional_successful_target_exit(
    launch_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """A genuine inherited4KiB limit refuses the final dataset despite a successful help exit."""
    if launch_view is None:
        return
    view, digest = launch_view
    result = _execute_entry(view, digest, tmp_path, small_file_limit=True)
    assert result.stdout.startswith("usage:")
    assert result.returncode != 0
    assert "dataset exceeds its byte bound" in result.stderr
    assert not list(tmp_path.iterdir())


def test_actual_manifest_disagreement_prevents_target_import_and_output(
    launch_view: tuple[Path, str] | None, tmp_path: Path
) -> None:
    """The fixed entry refuses changed captured pins before the target help can run."""
    if launch_view is None:
        return
    view, _digest = launch_view
    result = _execute_entry(view, "f" * 64, tmp_path)
    assert result.returncode != 0
    assert not result.stdout
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("role", "arguments", "namespace_root", "message"),
    [
        ("namespace-wrapper", ("--inside",), False, "entry mode differs from its role"),
        ("writer", ("--help",), False, "entry mode differs from its role"),
        ("writer", ("--inside",), True, "/instrumentation-data"),
    ],
)
def test_actual_fixed_entry_refuses_mismatched_mode_or_absent_writer_output_root(
    launch_view: tuple[Path, str] | None,
    tmp_path: Path,
    role: str,
    arguments: tuple[str, ...],
    namespace_root: bool,
    message: str,
) -> None:
    """Refuse real mode changes and an absent root inside a private single-UID namespace."""
    if launch_view is None:
        return
    view, digest = launch_view
    result = _execute_entry(
        view,
        digest,
        tmp_path,
        role=role,
        module="namespace_child",
        arguments=arguments,
        namespace_root=namespace_root,
    )
    assert result.returncode != 0 and message in result.stderr
    assert not result.stdout and not list(tmp_path.iterdir())


@pytest.mark.parametrize("change", ["role", "digest", "uid", "gid", "descriptor", "writer"])
def test_public_launch_enrollment_refuses_invalid_role_pin_or_descriptor(change: str) -> None:
    """Malformed public enrollment cannot turn a construction descriptor into recorder output."""
    role = "controller" if change == "role" else "writer" if change == "writer" else "client"
    digest = "not-a-pin" if change == "digest" else "a" * 64
    uid = True if change == "uid" else 1000
    gid = -1 if change == "gid" else 1000
    descriptor = 0 if change == "descriptor" else 9
    with pytest.raises(ValueError):
        RecordingLaunch(role, digest, uid, gid, descriptor)


def test_public_launch_selection_refuses_role_module_and_runtime_mismatch(tmp_path: Path) -> None:
    """A recorder cannot select another module or fall back to the host runtime profile."""
    enrollment = RecordingLaunch("client", "a" * 64, 1000, 1000, 9)
    with pytest.raises(ValueError, match="differs from its role"):
        enrollment.entry_arguments(tmp_path, "namespace_child")
    with pytest.raises(ValueError, match="differs from its role"):
        enrollment.entry_arguments(Path("relative"), "authority_child")
    with pytest.raises(ValueError, match="captured runtime"):
        probe_python_command(
            tmp_path, "authority_child", captured_runtime=False, recording=enrollment
        )


def test_fixed_target_exit_guards_keep_the_normal_return_unreachable() -> None:
    """Invalidate the exclusion if either target can return or the choices expand."""
    root = _SOURCE / "tools/protected_runtime_probe"
    launcher = ast.parse((root / "instrumentation_launch.py").read_text(encoding="utf-8"))
    module_arguments = [
        node
        for node in ast.walk(launcher)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "module"
    ]
    assert len(module_arguments) == 1
    choices = [item.value for item in module_arguments[0].keywords if item.arg == "choices"]
    assert len(choices) == 1
    assert set(ast.literal_eval(choices[0])) == {"authority_child", "namespace_child"}
    expected_test = ast.dump(ast.parse("__name__ == '__main__'", mode="eval").body)
    expected_exit = ast.dump(ast.parse("raise SystemExit(main())").body[0])
    for name in ("authority_child.py", "namespace_child.py"):
        module = ast.parse((root / name).read_text(encoding="utf-8"))
        guards = [
            node
            for node in module.body
            if isinstance(node, ast.If) and ast.dump(node.test) == expected_test
        ]
        assert len(guards) == 1 and not guards[0].orelse
        assert len(guards[0].body) == 1 and ast.dump(guards[0].body[0]) == expected_exit
