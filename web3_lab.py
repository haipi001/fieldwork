from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/v1/web3", tags=["Web3 local lab"])
FOUNDRY_BIN = Path.home() / ".foundry" / "bin"
LABS: dict[str, dict] = {}


class LocalForkInput(BaseModel):
    engagement_id: str
    chain_id: int = 31337


def start_rpc_fork(engagement_id: str, rpc_url: str, block_number: int | None = None, chain_id: int | None = None) -> dict:
    """Start an isolated Anvil fork. The upstream URL is kept in process memory only."""
    import final_core
    anvil = binary("anvil")
    if not anvil:
        raise HTTPException(409, "Anvil 未安装，无法启动本地 Fork")
    port = free_port()
    args = [anvil, "--host", "127.0.0.1", "--port", str(port), "--fork-url", rpc_url, "--silent"]
    if block_number is not None:
        args.extend(["--fork-block-number", str(block_number)])
    if chain_id is not None:
        args.extend(["--chain-id", str(chain_id)])
    fork = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        wait_ready(port, fork)
        resolved_chain = int(rpc(port, "eth_chainId"), 16)
        block = int(rpc(port, "eth_blockNumber"), 16)
    except Exception as error:
        stop_process(fork)
        raise HTTPException(409, f"RPC Fork 启动失败：{error}") from error
    lab_id = final_core.uid("fork")
    LABS[lab_id] = {"fork": fork, "fork_port": port}
    with final_core.connect() as db:
        db.execute("INSERT INTO web3_forks VALUES(?,?,?,?,?,?,?,?)", (
            lab_id, engagement_id, resolved_chain, "local_fork", block, "running", 1, final_core.utcnow(),
        ))
    return {"id": lab_id, "chain_id": resolved_chain, "fork_block": block, "status": "running", "network_class": "local_fork", "write_enabled": True, "uses_real_private_key": False}


class BalanceMutationInput(BaseModel):
    run_id: str
    address: str = "0x0000000000000000000000000000000000000001"
    balance_wei: int = 10**18


def binary(name: str) -> str | None:
    import shutil
    found = shutil.which(name)
    local = FOUNDRY_BIN / name
    return found or (str(local) if local.is_file() else None)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def rpc(port: int, method: str, params: list | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        body = json.loads(response.read())
    if "error" in body:
        raise RuntimeError(body["error"].get("message", "RPC error"))
    return body.get("result")


def wait_ready(port: int, process: subprocess.Popen) -> None:
    for _ in range(50):
        if process.poll() is not None:
            raise RuntimeError("Anvil exited before RPC became ready")
        try:
            rpc(port, "eth_chainId")
            return
        except Exception:
            time.sleep(.05)
    raise RuntimeError("Anvil RPC readiness timeout")


def stop_process(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def shutdown_labs() -> None:
    for lab in list(LABS.values()):
        stop_process(lab.get("fork"))
        stop_process(lab.get("upstream"))
    LABS.clear()


@router.post("/forks", status_code=201)
def start_local_fork(body: LocalForkInput):
    import final_core
    engagement = final_core.get_engagement(body.engagement_id)
    if engagement["mode"] != "web3":
        raise HTTPException(409, "本地 Fork 仅用于 Web3 Engagement")
    anvil = binary("anvil")
    if not anvil:
        raise HTTPException(409, "Anvil 未安装，Web3 local fork capability degraded")
    upstream_port, fork_port = free_port(), free_port()
    upstream = subprocess.Popen(
        [anvil, "--host", "127.0.0.1", "--port", str(upstream_port), "--chain-id", str(body.chain_id), "--silent"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    try:
        wait_ready(upstream_port, upstream)
        fork = subprocess.Popen(
            [anvil, "--host", "127.0.0.1", "--port", str(fork_port), "--chain-id", str(body.chain_id), "--fork-url", f"http://127.0.0.1:{upstream_port}", "--silent"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        wait_ready(fork_port, fork)
    except Exception as error:
        stop_process(upstream)
        raise HTTPException(500, f"Local fork failed: {error}") from error
    lab_id = final_core.uid("fork")
    LABS[lab_id] = {"upstream": upstream, "fork": fork, "upstream_port": upstream_port, "fork_port": fork_port}
    block = int(rpc(fork_port, "eth_blockNumber"), 16)
    with final_core.connect() as db:
        db.execute("INSERT INTO web3_forks VALUES(?,?,?,?,?,?,?,?)", (
            lab_id, body.engagement_id, body.chain_id, "local_fork", block, "running", 1, final_core.utcnow(),
        ))
    return {"id": lab_id, "engagement_id": body.engagement_id, "chain_id": body.chain_id, "fork_block": block, "status": "running", "network_class": "local_fork", "write_enabled": True, "uses_real_private_key": False}


@router.post("/forks/{fork_id}/mutate")
def mutate_local_fork(fork_id: str, body: BalanceMutationInput):
    import final_core
    lab = LABS.get(fork_id)
    if not lab:
        raise HTTPException(404, "Local fork 不存在或已停止")
    if not body.address.startswith("0x") or len(body.address) != 42:
        raise HTTPException(422, "无效 EVM address")
    with final_core.connect() as db:
        fork_row = db.execute("SELECT * FROM web3_forks WHERE id=?", (fork_id,)).fetchone()
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (body.run_id,)).fetchone()
    if not fork_row or not run or run["engagement_id"] != fork_row["engagement_id"]:
        raise HTTPException(409, "Fork state diff 必须绑定同一 Engagement 的 Run")
    port = lab["fork_port"]
    before = int(rpc(port, "eth_getBalance", [body.address, "latest"]), 16)
    rpc(port, "anvil_setBalance", [body.address, hex(body.balance_wei)])
    after = int(rpc(port, "eth_getBalance", [body.address, "latest"]), 16)
    with final_core.connect() as db:
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, body.run_id, run["engagement_id"], "web3", "web3.state_diff",
            body.address.lower(), f"balance {before} -> {after}", 1.0, "anvil", f"fork:{fork_id}", final_core.utcnow(),
        ))
    return {"fork_id": fork_id, "observation_id": observation_id, "address": body.address.lower(), "before_wei": before, "after_wei": after, "state_changed": before != after, "network_class": "local_fork", "broadcast": False}


@router.post("/forks/{fork_id}/stop")
def stop_local_fork(fork_id: str):
    import final_core
    lab = LABS.pop(fork_id, None)
    if not lab:
        raise HTTPException(404, "Local fork 不存在")
    stop_process(lab.get("fork"))
    stop_process(lab.get("upstream"))
    with final_core.connect() as db:
        db.execute("UPDATE web3_forks SET status='stopped' WHERE id=?", (fork_id,))
    return {"id": fork_id, "status": "stopped"}
