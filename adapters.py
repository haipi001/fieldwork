from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from dataclasses import dataclass
from typing import AsyncIterator

ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}


@dataclass(frozen=True)
class AdapterStatus:
    name: str
    kind: str
    available: bool
    configured: bool
    executable: str | None
    detail: str


def _configured_command(env_name: str) -> list[str] | None:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    args = shlex.split(raw)
    if not args:
        return None
    return args


def adapter_statuses() -> list[AdapterStatus]:
    strix_cmd = _configured_command("SRC_STRIX_COMMAND")
    verifier_cmd = _configured_command("SRC_PENTEST_AI_COMMAND")
    strix_executable = shutil.which(strix_cmd[0]) if strix_cmd else shutil.which("strix")
    verifier_binary = verifier_cmd[0] if verifier_cmd else "pentest-ai"
    verifier_executable = shutil.which(verifier_binary) or shutil.which("pentest_ai")
    return [
        AdapterStatus("demo", "runner", True, True, None, "内置安全演示运行器，不发起真实网络请求"),
        AdapterStatus(
            "strix", "runner", bool(strix_executable and strix_cmd), bool(strix_cmd), strix_executable,
            "已就绪" if strix_executable and strix_cmd else "需要安装 Strix 并配置 SRC_STRIX_COMMAND",
        ),
        AdapterStatus(
            "pentest-ai", "verifier", bool(verifier_executable and verifier_cmd), bool(verifier_cmd), verifier_executable,
            "已就绪" if verifier_executable and verifier_cmd else "需要安装验证器并配置 SRC_PENTEST_AI_COMMAND",
        ),
    ]


def build_command(adapter: str, engagement_id: str, target: str) -> list[str]:
    env_name = {"strix": "SRC_STRIX_COMMAND", "pentest-ai": "SRC_PENTEST_AI_COMMAND"}.get(adapter)
    if not env_name:
        raise ValueError(f"未知 Adapter：{adapter}")
    template = _configured_command(env_name)
    if not template:
        raise RuntimeError(f"{env_name} 未配置")
    values = {"engagement_id": engagement_id, "target": target}
    return [part.format_map(values) for part in template]


async def stop_process(process_key: str) -> bool:
    process = ACTIVE_PROCESSES.get(process_key)
    if not process or process.returncode is not None:
        return False
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        process.kill()
        await process.wait()
    return True


async def stream_process(args: list[str], timeout_seconds: float, process_key: str) -> AsyncIterator[tuple[str, str]]:
    """Run an argv-only subprocess and stream labelled output. Never invokes a shell."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "NO_COLOR": "1"},
    )
    ACTIVE_PROCESSES[process_key] = process

    async def pump(stream: asyncio.StreamReader, label: str, queue: asyncio.Queue):
        while line := await stream.readline():
            await queue.put((label, line.decode(errors="replace").rstrip()))
        await queue.put((label, None))

    queue: asyncio.Queue = asyncio.Queue()
    tasks = [asyncio.create_task(pump(process.stdout, "stdout", queue)), asyncio.create_task(pump(process.stderr, "stderr", queue))]
    closed = 0
    try:
        async with asyncio.timeout(timeout_seconds):
            while closed < 2:
                label, line = await queue.get()
                if line is None:
                    closed += 1
                else:
                    yield label, line
            code = await process.wait()
            yield "exit", str(code)
    except TimeoutError:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        yield "timeout", "Adapter 超过运行时间预算并被终止"
    finally:
        ACTIVE_PROCESSES.pop(process_key, None)
        for task in tasks:
            if not task.done():
                task.cancel()
