import os
import sys
import tempfile
from pathlib import Path

from isolated_execution import run_isolated
import pytest


def test_missing_sandbox_fails_closed_before_running_project(tmp_path, monkeypatch):
    import isolated_execution

    script = tmp_path / "would-run.py"
    marker = tmp_path / "ran.txt"
    script.write_text(f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')")
    monkeypatch.setattr(isolated_execution.sys, "platform", "linux")
    with pytest.raises(RuntimeError, match="refusing unsandboxed execution"):
        run_isolated([sys.executable, str(script)], tmp_path)
    assert not marker.exists()


def test_isolated_process_does_not_inherit_secret_and_caps_output(tmp_path, monkeypatch):
    monkeypatch.setenv("FIELDWORK_SENTINEL_SECRET", "must-not-leak")
    script = tmp_path / "probe.py"
    script.write_text("import os; print(os.getenv('FIELDWORK_SENTINEL_SECRET')); print('x'*5000)")
    result = run_isolated([sys.executable, str(script)], tmp_path, output_limit=1024)
    assert result.returncode == 0
    assert "must-not-leak" not in result.stdout
    assert "None" in result.stdout
    assert len(result.stdout) < 1200
    assert "output truncated" in result.stdout


def test_macos_sandbox_denies_file_outside_workspace_and_network(tmp_path):
    if not Path("/usr/bin/sandbox-exec").is_file():
        return
    sentinel = Path.home() / f".fieldwork-host-sentinel-{os.getpid()}.txt"
    sentinel.write_text("host-secret")
    work = tmp_path / "project"
    work.mkdir()
    script = work / "probe.py"
    script.write_text(
        "from pathlib import Path\n"
        "import socket\n"
        f"\ntry: print('READ', Path({str(sentinel)!r}).read_text())\nexcept Exception as e: print('READ_DENIED', type(e).__name__)\n"
        "try: socket.create_connection(('127.0.0.1', 9), timeout=.2); print('NET_ALLOWED')\n"
        "except Exception as e: print('NET_DENIED', type(e).__name__)\n"
    )
    try:
        result = run_isolated([sys.executable, str(script)], work)
        assert result.returncode == 0
        assert result.isolation == "macos-sandbox"
        assert "host-secret" not in result.stdout
        assert "READ_DENIED" in result.stdout
        assert "NET_DENIED" in result.stdout
    finally:
        sentinel.unlink(missing_ok=True)


def test_macos_sandbox_denies_other_temp_files_and_external_writes(tmp_path):
    if not Path("/usr/bin/sandbox-exec").is_file():
        return
    with tempfile.TemporaryDirectory(prefix="fieldwork-host-sentinel-", dir="/private/tmp") as outside:
        sentinel = Path(outside) / "secret.txt"
        sentinel.write_text("host-secret")
        project = tmp_path / "project"
        project.mkdir()
        (project / "probe.py").write_text(
            "from pathlib import Path\n"
            f"p = Path({str(sentinel)!r})\n"
            "try: print('READ', p.read_text())\n"
            "except Exception as e: print('READ_DENIED', type(e).__name__)\n"
            "try: p.write_text('changed'); print('WRITE_ALLOWED')\n"
            "except Exception as e: print('WRITE_DENIED', type(e).__name__)\n"
        )
        result = run_isolated([sys.executable, "probe.py"], project)
        assert result.returncode == 0
        assert "READ_DENIED" in result.stdout
        assert "WRITE_DENIED" in result.stdout
        assert sentinel.read_text() == "host-secret"


@pytest.mark.parametrize("setting", ["ffi = true", 'fs_permissions = [{ access = "read", path = "../" }]'])
def test_untrusted_foundry_escape_configuration_is_rejected(tmp_path, setting):
    (tmp_path / "foundry.toml").write_text(f"[profile.default]\n{setting}\n")
    with pytest.raises(ValueError, match="forbidden"):
        run_isolated([sys.executable, "-c", "print('must not run')"], tmp_path)


def test_export_directory_rejects_symlink_root_special_files_and_bounds(tmp_path):
    import os
    import pytest
    from isolated_execution import _export_directory
    outside=tmp_path/'outside';outside.mkdir();(outside/'owned.txt').write_text('owned fixture')
    root=tmp_path/'export';root.symlink_to(outside,target_is_directory=True)
    destination=tmp_path/'destination'
    with pytest.raises(ValueError,match='root is a symlink'):_export_directory(root,destination)
    assert not destination.exists()
    root.unlink();root.mkdir();os.mkfifo(root/'fifo')
    with pytest.raises(ValueError,match='special file'):_export_directory(root,destination)
    (root/'fifo').unlink();(root/'large').write_bytes(b'x'*1025)
    with pytest.raises(ValueError,match='byte limit'):_export_directory(root,destination,max_bytes=1024)
    assert not destination.exists()


@pytest.mark.parametrize('limit',[0,-1,True,1000001])
def test_explicit_export_limit_never_exceeds_current_global_cap(tmp_path,monkeypatch,limit):
    import isolated_execution,sys
    monkeypatch.setattr(isolated_execution,'MAX_SNAPSHOT_BYTES',1000000)
    with pytest.raises(ValueError,match='invalid isolated export byte limit'):
        isolated_execution.run_isolated([sys.executable,'-c','pass'],tmp_path,export_limit_bytes=limit)
