from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from reporting import redact


@dataclass(frozen=True)
class Capability:
    id: str
    domain: str
    executable: str | None
    available: bool
    version: str | None
    license: str
    detail: str
    heavy_optional: bool = True


@dataclass(frozen=True)
class ToolResultEnvelope:
    capability: str
    status: Literal["completed", "failed", "timeout", "unavailable"]
    exit_code: int | None
    stdout: str
    stderr: str
    synthetic: bool = False


SPECS = {
    "strix": ("traditional", "strix", "Apache-2.0", "Autonomous security agent"),
    "shannon": ("traditional", "shannon", "AGPL-3.0", "Source-aware proof-by-exploitation Web/API agent"),
    "pentest-ai": ("verification", "ptai", "MIT", "Deterministic HTTP oracle and proof capsules"),
    "nuclei": ("traditional", "nuclei", "MIT", "Template scanner"),
    "katana": ("traditional", "katana", "MIT", "Web crawler"),
    "httpx": ("traditional", "httpx", "MIT", "HTTP probe"),
    "subfinder": ("traditional", "subfinder", "MIT", "Subdomain discovery"),
    "semgrep": ("code", "semgrep", "LGPL-2.1", "Static analysis"),
    "gitleaks": ("code", "gitleaks", "MIT", "Secret scanning"),
    "trivy": ("code", "trivy", "Apache-2.0", "Dependency and image analysis"),
    "forge": ("web3", "forge", "MIT/Apache-2.0", "Foundry build and fuzz"),
    "anvil": ("web3", "anvil", "MIT/Apache-2.0", "Local EVM fork"),
    "cast": ("web3", "cast", "MIT/Apache-2.0", "EVM RPC utility"),
    "slither": ("web3", "slither", "AGPL-3.0", "Solidity static analyzer"),
    "aderyn": ("web3", "aderyn", "BUSL-1.1", "Rust Solidity analyzer"),
    "echidna": ("web3", "echidna", "AGPL-3.0", "Property fuzzer"),
    "medusa": ("web3", "medusa", "AGPL-3.0", "Parallel Solidity fuzzer"),
    "halmos": ("web3", "halmos", "AGPL-3.0", "Symbolic testing"),
}

_INVENTORY_CACHE: tuple[float, list[dict]] | None = None


def resolve_executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    # Finder-launched apps inherit a minimal PATH. Probe standard package
    # manager and runtime locations explicitly so desktop and shell agree.
    candidates = (
        Path(sys.executable).resolve().parent / name,
        Path("/opt/homebrew/bin") / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".foundry" / "bin" / name,
        Path.home() / ".local" / "bin" / name,
        Path.home() / ".cargo" / "bin" / name,
        Path.home() / "anaconda3" / "bin" / name,
        Path.home() / ".strix" / "bin" / name,
        Path.home() / ".hermes" / "node" / "bin" / name,
    )
    return next((str(candidate) for candidate in candidates if candidate.is_file()), None)


def executable_candidates(name: str) -> list[str]:
    candidates = [resolve_executable(name)]
    for prefix in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        candidate = prefix / name
        if candidate.is_file() and str(candidate) not in candidates:
            candidates.append(str(candidate))
    hermes_candidate = Path.home() / ".hermes" / "node" / "bin" / name
    if hermes_candidate.is_file() and str(hermes_candidate) not in candidates:
        candidates.append(str(hermes_candidate))
    return [candidate for candidate in candidates if candidate]


def clean_version(output: str) -> str | None:
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", output)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    preferred = next((line for line in lines if "version" in line.lower()), None)
    return redact((preferred or (lines[0] if lines else ""))[:200]) or None


def detect(capability_id: str) -> Capability:
    domain, command, license_name, detail = SPECS[capability_id]
    executable = None
    version = None
    for candidate in executable_candidates(command):
        try:
            version_arg = "-version" if capability_id == "httpx" else "--version"
            result = subprocess.run([candidate, version_arg], capture_output=True, text=True, timeout=8)
            output = (result.stdout + "\n" + result.stderr).strip()
            if capability_id == "httpx" and not any(marker in output.lower() for marker in ("projectdiscovery", "current version", "httpx version")):
                continue
            else:
                executable = candidate
                version = clean_version(output)
                break
        except (OSError, subprocess.TimeoutExpired, IndexError):
            continue
    if executable and not version:
        version = "installed/version-unavailable"
    return Capability(capability_id, domain, executable, bool(executable), version, license_name, detail)


def inventory(refresh: bool = False) -> list[dict]:
    """Return a short-lived version probe snapshot so plan dialogs stay responsive."""
    global _INVENTORY_CACHE
    now = time.monotonic()
    if not refresh and _INVENTORY_CACHE and now - _INVENTORY_CACHE[0] < 60:
        return [dict(item) for item in _INVENTORY_CACHE[1]]
    values = [asdict(detect(name)) for name in SPECS]
    _INVENTORY_CACHE = (now, values)
    return [dict(item) for item in values]


def execute(capability_id: str, args: list[str], cwd: Path, timeout: int = 120) -> ToolResultEnvelope:
    if capability_id not in SPECS:
        raise ValueError("unknown capability")
    capability = detect(capability_id)
    if not capability.executable:
        return ToolResultEnvelope(capability_id, "unavailable", None, "", "capability unavailable")
    if not cwd.is_dir():
        raise ValueError("cwd must be an existing directory")
    env = os.environ.copy()
    extra_paths = [str(Path(sys.executable).resolve().parent), str(Path.home() / ".foundry" / "bin")]
    env["PATH"] = os.pathsep.join([*extra_paths, env.get("PATH", "")])
    try:
        result = subprocess.run([capability.executable, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, shell=False, env=env)
        # Slither returns 255 when detectors report findings; that is a
        # successful analysis result, not an adapter failure.
        accepted = result.returncode == 0 or (capability_id == "slither" and result.returncode == 255) or (capability_id == "gitleaks" and result.returncode == 1)
        output_limit = 1_000_000 if capability_id in {"slither", "echidna", "medusa"} else 12000
        return ToolResultEnvelope(capability_id, "completed" if accepted else "failed", result.returncode, redact(result.stdout[-output_limit:]), redact(result.stderr[-output_limit:]))
    except subprocess.TimeoutExpired as error:
        return ToolResultEnvelope(capability_id, "timeout", None, redact(error.stdout or ""), redact(error.stderr or "execution timed out"))
