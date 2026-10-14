"""Fail-closed local execution boundary for untrusted project tools."""
from __future__ import annotations

import os
import plistlib
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path


SAFE_ENV_KEYS = {"LANG", "LC_ALL", "LC_CTYPE", "TERM"}
DEFAULT_OUTPUT_LIMIT = 1_000_000
MAX_SNAPSHOT_FILES = 10_000
MAX_SNAPSHOT_BYTES = 250 * 1024 * 1024
SNAPSHOT_EXCLUDES = {".git", ".venv", "node_modules", "out", "cache", "broadcast"}


def _requested_solc(root: Path) -> str | None:
    config = root / "foundry.toml"
    if not config.is_file():
        return None
    try:
        document = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    profile = document.get("profile", {}).get("default", {})
    value = profile.get("solc_version") or profile.get("solc")
    return str(value).removeprefix("v") if value else None


def _trusted_solc(root: Path) -> Path | None:
    requested = _requested_solc(root)
    candidates = [Path("/opt/homebrew/bin/solc"), Path("/usr/local/bin/solc")]
    if requested:
        candidates.insert(0, Path.home() / ".solc-select" / "artifacts" / f"solc-{requested}" / f"solc-{requested}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


@dataclass(frozen=True)
class IsolatedResult:
    returncode: int | None
    stdout: str
    stderr: str
    status: str
    isolation: str


def _quote_profile(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sandbox_profile(cwd: Path, home: Path, executable: Path) -> str:
    readable = [str(cwd), str(home), str(executable.parent)]
    if executable.name.startswith("python"):
        # Python needs its trusted standard library (including encodings) before
        # the untrusted workspace script can even start. Do not allow the whole
        # user home, which may contain credentials or unrelated project files.
        for key in ("stdlib", "platstdlib", "purelib", "platlib"):
            path = sysconfig.get_path(key)
            if path and Path(path).is_dir():
                readable.append(str(Path(path).resolve()))
    solc = _trusted_solc(cwd)
    if solc:
        readable.append(str(solc.parent))
    for optional in (Path.home() / ".foundry", Path.home() / ".svm", Path.home() / ".solc-select"):
        if optional.exists():
            readable.append(str(optional.resolve()))
    read_rules = " ".join(f"(subpath {_quote_profile(path)})" for path in dict.fromkeys(readable))
    write_rules = " ".join(f"(subpath {_quote_profile(str(path))})" for path in (cwd, home))
    denied_roots = [str(Path.home().resolve()), "/Volumes", "/private/tmp", "/tmp"]
    deny_rules = " ".join(f"(subpath {_quote_profile(path)})" for path in dict.fromkeys(denied_roots))
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-read* {deny_rules})
(allow file-read* {read_rules})
(deny file-write*)
(allow file-write* {write_rules})
"""


def _limits() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))


def clean_environment(home: Path, executable: Path, project_root: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in SAFE_ENV_KEYS if key in os.environ}
    tool_paths = [str(executable.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    env.update({
        "HOME": str(home), "TMPDIR": str(home / "tmp"), "NO_COLOR": "1",
        "PATH": os.pathsep.join(dict.fromkeys(tool_paths)),
        "FOUNDRY_FFI": "false", "FOUNDRY_OFFLINE": "true", "FOUNDRY_DISABLE_NIGHTLY_WARNING": "1",
    })
    if executable.name == "forge" and (solc := _trusted_solc(project_root)):
        env["FOUNDRY_SOLC"] = str(solc)
        env["PATH"] = os.pathsep.join([str(solc.parent), env["PATH"]])
    return env


def _copy_workspace(source: Path, destination: Path) -> None:
    count = 0
    total = 0
    destination.mkdir(mode=0o700)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in SNAPSHOT_EXCLUDES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"workspace symlink is not allowed: {relative}")
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not path.is_file():
            raise ValueError(f"unsupported workspace entry: {relative}")
        count += 1
        total += path.stat().st_size
        if count > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
            raise ValueError("workspace exceeds isolated snapshot limits")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def validate_project_policy(root: Path) -> None:
    config = root / "foundry.toml"
    if not config.is_file():
        return
    try:
        document = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError("invalid foundry.toml") from error

    def inspect(value) -> None:
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized == "ffi" and item is True:
                raise ValueError("Foundry FFI is forbidden for untrusted projects")
            if normalized == "fs_permissions" and item:
                raise ValueError("Foundry fs_permissions are forbidden for untrusted projects")
            inspect(item)

    inspect(document)


def _export_directory(snapshot: Path, destination: Path) -> None:
    if not snapshot.is_dir():
        return
    if any(path.is_symlink() for path in snapshot.rglob("*")):
        raise ValueError("isolated output contains a symlink")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(snapshot, destination)


def run_isolated(argv: list[str], cwd: Path, *, timeout: int = 120,
                 output_limit: int = DEFAULT_OUTPUT_LIMIT,
                 export_dirs: tuple[str, ...] = ()) -> IsolatedResult:
    if not argv or timeout < 1 or output_limit < 1024:
        raise ValueError("invalid isolated execution request")
    root = cwd.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("cwd must be an existing directory")
    sandbox = Path("/usr/bin/sandbox-exec")
    if sys.platform != "darwin" or not sandbox.is_file():
        raise RuntimeError("isolated execution requires the macOS sandbox; refusing unsandboxed execution")
    validate_project_policy(root)
    executable_text = shutil.which(argv[0]) if not Path(argv[0]).is_absolute() else argv[0]
    if not executable_text:
        raise FileNotFoundError(argv[0])
    executable = Path(executable_text).resolve()
    if not executable.is_file():
        raise FileNotFoundError(str(executable))

    with tempfile.TemporaryDirectory(prefix="fieldwork-isolated-") as temp:
        home = Path(temp).resolve()
        (home / "tmp").mkdir(mode=0o700)
        execution_root = home / "workspace"
        _copy_workspace(root, execution_root)
        mapped_args = []
        for value in argv[1:]:
            candidate = Path(value).expanduser()
            if candidate.is_absolute():
                try:
                    value = str(execution_root / candidate.resolve().relative_to(root))
                except ValueError:
                    pass
            mapped_args.append(value)
        command = [str(executable), *mapped_args]
        profile = home / "sandbox.sb"
        profile.write_text(_sandbox_profile(execution_root, home, executable))
        command = [str(sandbox), "-f", str(profile), *command]
        isolation = "macos-sandbox"

        process = subprocess.Popen(
            command, cwd=execution_root, env=clean_environment(home, executable, execution_root), shell=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, preexec_fn=_limits,
        )
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        deadline = time.monotonic() + timeout
        timed_out = False
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                break
            for key, _ in selector.select(timeout=min(0.2, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = captured[key.data]
                remaining = output_limit - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated[key.data] = True
        if timed_out:
            process.wait(timeout=5)
        else:
            process.wait()
        suffix = b"\n[output truncated by Fieldwork]" if any(truncated.values()) else b""
        decode = lambda data: (bytes(data) + suffix).decode("utf-8", errors="replace")
        result = IsolatedResult(
            None if timed_out else process.returncode, decode(captured["stdout"]),
            decode(captured["stderr"]), "timeout" if timed_out else "completed", isolation,
        )
        if not timed_out:
            for relative in export_dirs:
                if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
                    raise ValueError("invalid isolated export path")
                _export_directory(execution_root / relative, root / relative)
        return result
