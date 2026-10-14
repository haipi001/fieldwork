"""Expose the source tree to isolated test subprocesses as well as pytest."""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_existing = os.environ.get("PYTHONPATH", "")
os.environ["PYTHONPATH"] = os.pathsep.join([str(_ROOT / "backend"), str(_ROOT), *([_existing] if _existing else [])])


import shutil
import subprocess
import pytest


@pytest.fixture(scope="session")
def compiled_vault_fixture():
    """Prepare the optional Solidity fixture instead of relying on ignored output."""
    forge = shutil.which("forge")
    if not forge:
        candidate = Path.home() / ".foundry/bin/forge"
        forge = str(candidate) if candidate.is_file() else None
    if not forge:
        pytest.skip("Forge is required for deployment-alignment fixtures")
    directory = _ROOT / "fixtures/web3-vault"
    result = subprocess.run([forge, "build", "--no-lint"], cwd=directory,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (directory / "out/Vault.sol/Vault.json").is_file()
    return directory
