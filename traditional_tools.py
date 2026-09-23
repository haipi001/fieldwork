from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import capability_registry
from reporting import redact


router = APIRouter(prefix="/api/v1/traditional", tags=["Traditional toolchain"])
ARTIFACT_ROOT = Path(__file__).resolve().parent / "data" / "artifacts"
NETWORK_CAPABILITIES = ("subfinder", "httpx", "katana", "nuclei")
CODE_CAPABILITIES = ("semgrep", "gitleaks", "trivy")
AGENT_CAPABILITIES = ("strix", "shannon")
TRADITIONAL_CAPABILITIES = (*NETWORK_CAPABILITIES, *CODE_CAPABILITIES, *AGENT_CAPABILITIES)
REPOSITORY_ROOT = Path(__file__).resolve().parent / "data" / "repositories"


class TraditionalToolInput(BaseModel):
    capability: Literal["subfinder", "httpx", "katana", "nuclei", "semgrep", "gitleaks", "trivy", "strix", "shannon"]
    source_path: str | None = None
    timeout_seconds: int = Field(default=120, ge=5, le=7200)


class TraditionalToolchainInput(BaseModel):
    include_recon: bool = True
    include_code: bool = False
    source_path: str | None = None
    timeout_seconds: int = Field(default=120, ge=5, le=7200)
    max_discovered_targets: int = Field(default=25, ge=1, le=200)
    scan_profile: Literal["quick", "standard", "deep"] = "quick"
    include_strix: bool = False
    include_shannon: bool = False
    include_native_agent: bool = True


class StrixProviderInput(BaseModel):
    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str = Field(min_length=8, max_length=4096)


def command_for(capability: str, target: str, source_path: str | None = None, rate_limit: int = 1, scan_profile: str = "quick", model_budget_usd: float = 1) -> list[str]:
    rate = str(max(1, min(int(rate_limit), 150)))
    if capability == "subfinder":
        host = urlparse(target).hostname
        if not host:
            raise ValueError("subfinder requires a URL/host target")
        return ["-d", host, "-json", "-silent", "-rl", rate]
    if capability == "httpx":
        return ["-u", target, "-json", "-silent", "-rl", rate]
    if capability == "katana":
        depth = {"quick": "1", "standard": "2", "deep": "3"}.get(scan_profile, "1")
        duration = {"quick": "30s", "standard": "2m", "deep": "5m"}.get(scan_profile, "30s")
        return ["-u", target, "-jsonl", "-silent", "-d", depth, "-ct", duration, "-rl", rate, "-or", "-ob"]
    if capability == "nuclei":
        args = ["-u", target, "-jsonl", "-silent", "-severity", "info,low,medium,high,critical", "-rl", rate, "-bs", "1", "-c", "1", "-timeout", "8", "-retries", "0", "-etags", "dos,fuzz,intrusive"]
        template_root = Path.home() / "nuclei-templates" / "http"
        profiles = {
            "quick": ["technologies/tech-detect.yaml"],
            "standard": [
                "technologies/tech-detect.yaml", "misconfiguration/http-missing-security-headers.yaml",
                "miscellaneous/robots-txt.yaml", "vulnerabilities/generic/cors-misconfig.yaml",
            ],
        }
        selected = profiles.get(scan_profile)
        if selected and all((template_root / item).is_file() for item in selected):
            for item in selected:
                args.extend(["-t", str(template_root / item)])
        elif scan_profile != "deep":
            args.extend(["-tags", "tech"])
        return args
    if capability == "strix":
        turns = {"quick": "50", "standard": "150", "deep": "500"}.get(scan_profile, "50")
        return ["-n", "-t", target, "--scan-mode", scan_profile, "--scope-mode", "full", "--max-budget", str(max(.01, model_budget_usd)), "--max-turns", turns]
    if capability == "shannon":
        if not source_path:
            raise ValueError("Shannon requires a local source repository")
        return ["start", "--url", target, "--repo", source_path, "--follow"]
    if not source_path:
        raise ValueError(f"{capability} requires source_path")
    if capability == "semgrep":
        local_config = Path(source_path) / ".semgrep.yml"
        config = str(local_config) if local_config.is_file() else "auto"
        return ["scan", "--json", "--config", config, source_path]
    if capability == "gitleaks":
        return ["detect", "--no-git", "--source", source_path, "--report-format", "json", "--report-path", "-"]
    if capability == "trivy":
        return ["fs", "--format", "json", "--scanners", "vuln,secret,misconfig", source_path]
    raise ValueError("unsupported traditional capability")


def _json_values(text: str) -> list[Any]:
    stripped = text.strip()
    if not stripped:
        return []
    try:
        value = json.loads(stripped)
        return value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        values = []
        for line in stripped.splitlines():
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return values


def _observation(kind: str, subject: str, summary: str, confidence: float, structured: dict[str, Any]) -> dict[str, Any]:
    return {
        "observation_type": kind,
        "subject": subject,
        "summary": redact(summary),
        "confidence": confidence,
        "structured": sanitize_structured(structured),
    }


def sanitize_structured(value: Any) -> Any:
    blocked = {"request", "response", "headers", "body", "curl-command", "secret", "match", "token", "authorization", "cookie", "set-cookie"}
    if isinstance(value, dict):
        return {key: sanitize_structured(item) for key, item in value.items() if key.lower() not in blocked}
    if isinstance(value, list):
        return [sanitize_structured(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def parse_output(capability: str, stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    values = _json_values(stdout)
    observations: list[dict[str, Any]] = []
    if capability == "subfinder":
        for item in values:
            host = item.get("host") or item.get("input") if isinstance(item, dict) else str(item)
            if host:
                observations.append(_observation("asset.hostname", str(host), f"Discovered hostname {host}", .65, item if isinstance(item, dict) else {"host": host}))
    elif capability == "httpx":
        for item in values:
            if not isinstance(item, dict):
                continue
            subject = item.get("url") or item.get("input") or "unknown endpoint"
            status = item.get("status_code") or item.get("status-code")
            title = item.get("title")
            observations.append(_observation("http.service", str(subject), f"HTTP {status or 'response'}{f' · {title}' if title else ''}", .75, item))
    elif capability == "katana":
        for item in values:
            if not isinstance(item, dict):
                continue
            request = item.get("request") if isinstance(item.get("request"), dict) else {}
            subject = request.get("endpoint") or item.get("url") or item.get("endpoint")
            if subject:
                observations.append(_observation("surface.route", str(subject), f"Crawler discovered {subject}", .65, item))
    elif capability == "nuclei":
        for item in values:
            if not isinstance(item, dict):
                continue
            info = item.get("info") if isinstance(item.get("info"), dict) else {}
            subject = item.get("matched-at") or item.get("host") or item.get("url") or "unknown target"
            name = info.get("name") or item.get("template-id") or "Nuclei template match"
            severity = info.get("severity") or "unknown"
            observations.append(_observation("scanner.template_match", str(subject), f"{name} [{severity}]", .7, item))
    elif capability == "semgrep":
        root = values[0] if values and isinstance(values[0], dict) else {}
        for item in root.get("results", []):
            extra = item.get("extra", {})
            subject = f"{item.get('path', 'unknown')}:{item.get('start', {}).get('line', '?')}"
            observations.append(_observation("code.static_finding", subject, extra.get("message") or item.get("check_id", "Semgrep match"), .7, item))
        if root and not observations and isinstance(root.get("results"), list):
            observations.append(_observation("code.static_scan_clean", "source tree", "Semgrep completed with no rule matches", .9, {"errors": root.get("errors", [])}))
    elif capability == "gitleaks":
        for item in values:
            if not isinstance(item, dict):
                continue
            subject = f"{item.get('File', item.get('file', 'unknown'))}:{item.get('StartLine', item.get('startLine', '?'))}"
            rule = item.get("RuleID") or item.get("Description") or "secret candidate"
            safe = {k: v for k, v in item.items() if k.lower() not in {"secret", "match"}}
            observations.append(_observation("code.secret_candidate", subject, f"Gitleaks match: {rule}", .8, safe))
        if not observations and "no leaks found" in stderr.lower():
            observations.append(_observation("code.secret_scan_clean", "source tree", "Gitleaks completed with no secret candidates", .9, {}))
    elif capability == "trivy":
        root = values[0] if values and isinstance(values[0], dict) else {}
        for result in root.get("Results", []):
            target = result.get("Target", "unknown")
            for item in result.get("Vulnerabilities") or []:
                vuln = item.get("VulnerabilityID", "dependency vulnerability")
                observations.append(_observation("code.dependency_vulnerability", target, f"{vuln} [{item.get('Severity', 'UNKNOWN')}]", .8, item))
            for item in result.get("Misconfigurations") or []:
                observations.append(_observation("code.misconfiguration", target, item.get("Title") or item.get("ID", "misconfiguration"), .7, item))
            for item in result.get("Secrets") or []:
                safe = {k: v for k, v in item.items() if k.lower() not in {"match", "secret"}}
                observations.append(_observation("code.secret_candidate", target, item.get("Title") or item.get("RuleID", "secret candidate"), .8, safe))
        if root and not observations and root.get("SchemaVersion"):
            observations.append(_observation("code.supply_chain_scan_clean", root.get("ArtifactName", "source tree"), "Trivy completed with no vulnerability, secret, or misconfiguration records", .9, {"SchemaVersion": root.get("SchemaVersion"), "ArtifactType": root.get("ArtifactType")}))
    elif capability == "strix":
        observations.append(_observation("agent.runtime_result", "Strix run", "Strix runtime completed; structured run artifacts are evaluated separately", .4, {}))
    if not observations and (stdout.strip() or stderr.strip()):
        observations.append(_observation("tool.unparsed_output", capability, f"{capability} returned output that produced no structured records", .2, {"stderr": redact(stderr[-1000:])}))
    return observations


def strix_config_path() -> Path:
    return Path.home() / ".strix" / "cli-config.json"


def shannon_config_path() -> Path:
    return strix_config_path().parent.parent / ".shannon" / "config.toml"


def strix_provider_settings() -> dict[str, str]:
    values: dict[str, str] = {}
    config = strix_config_path()
    try:
        payload = json.loads(config.read_text()) if config.is_file() and config.stat().st_size <= 64_000 else {}
        env = payload.get("env", {}) if isinstance(payload, dict) else {}
        if isinstance(env, dict):
            values.update({str(k): str(v) for k, v in env.items() if isinstance(v, (str, int, float))})
    except (OSError, json.JSONDecodeError):
        pass
    for name in ("STRIX_LLM", "LLM_API_BASE", "OPENAI_API_BASE", "LITELLM_BASE_URL", "OLLAMA_API_BASE", "LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def strix_configured() -> bool:
    values = strix_provider_settings()
    model = values.get("STRIX_LLM", "").strip()
    if not model:
        return False
    if values.get("LLM_API_KEY") or values.get("OPENAI_API_KEY") or values.get("ANTHROPIC_API_KEY"):
        return True
    base = values.get("LLM_API_BASE") or values.get("OPENAI_API_BASE") or values.get("LITELLM_BASE_URL") or values.get("OLLAMA_API_BASE")
    return bool(base and model.startswith("ollama/"))


def validate_provider_base(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, "API Base 必须是无内嵌凭据、查询参数或片段的 HTTP(S) URL")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise HTTPException(422, "远程 Provider 必须使用 HTTPS；HTTP 只允许本机模型服务")
    return normalized


def save_strix_provider(body: StrixProviderInput) -> dict[str, Any]:
    base = validate_provider_base(body.base_url)
    model = body.model.strip()
    if any(char.isspace() for char in model) or not model:
        raise HTTPException(422, "模型 ID 不能为空或包含空白字符")
    strix_model = model if model.startswith(("openai/", "anthropic/", "ollama/", "vertex_ai/", "bedrock/")) else f"openai/{model}"
    destination = strix_config_path()
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {"env": {
        "STRIX_LLM": strix_model,
        "LLM_API_BASE": base,
        "LLM_API_KEY": body.api_key,
        "STRIX_REASONING_EFFORT": "medium",
        "LLM_TIMEOUT": "600",
        "STRIX_TELEMETRY": "false",
    }}
    descriptor, temporary_name = tempfile.mkstemp(prefix="cli-config-", suffix=".json", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    shannon_model_id = model.removeprefix("openai/").removeprefix("openai:")
    shannon_destination = shannon_config_path()
    shannon_destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shannon_payload = (
        "[core]\n"
        f"model = {json.dumps(f'openai:{shannon_model_id}')}\n"
        f"base_url = {json.dumps(base)}\n\n"
        "[openai]\n"
        f"api_key = {json.dumps(body.api_key)}\n"
        'format = "chat-completions"\n'
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix="config-", suffix=".toml", dir=shannon_destination.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(shannon_payload)
        os.replace(temporary, shannon_destination)
        shannon_destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {"configured": True, "base_url": base, "model": strix_model, "has_api_key": True}


@router.get("/provider")
def get_strix_provider():
    values = strix_provider_settings()
    return {
        "configured": strix_configured() or shannon_configured(),
        "base_url": values.get("LLM_API_BASE") or values.get("OPENAI_API_BASE") or "",
        "model": values.get("STRIX_LLM", ""),
        "has_api_key": bool(values.get("LLM_API_KEY") or values.get("OPENAI_API_KEY") or values.get("ANTHROPIC_API_KEY")),
    }


@router.put("/provider")
def put_strix_provider(body: StrixProviderInput):
    return save_strix_provider(body)


def strix_sandbox_ready() -> bool:
    """Return whether the pinned Strix sandbox image is locally usable.

    This is deliberately a local, bounded check: capability discovery must not
    pull images, contact a registry, or leave a Docker process running.
    """
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        result = subprocess.run(
            [docker, "image", "inspect", "ghcr.io/usestrix/strix-sandbox:1.3.0"],
            capture_output=True,
            text=True,
            timeout=3,
            shell=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def shannon_configured() -> bool:
    return shannon_config_path().is_file() or bool(
        os.environ.get("SHANNON_AI_MODEL")
        and (os.environ.get("SHANNON_AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    )


def shannon_ready() -> bool:
    docker = shutil.which("docker")
    if not capability_registry.detect("shannon").available or not docker or not shannon_configured():
        return False
    try:
        return subprocess.run([docker, "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def latest_strix_run(root: Path, started_at: float) -> Path | None:
    run_root = root / "strix_runs"
    if not run_root.is_dir():
        return None
    candidates = [path for path in run_root.iterdir() if path.is_dir() and path.stat().st_mtime >= started_at - 2]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def parse_strix_artifacts(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "vulnerabilities.json"
    if not path.is_file() or path.stat().st_size > 5_000_000:
        return []
    try:
        payload = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        items = payload.get("vulnerabilities") or payload.get("findings") or payload.get("results") or []
    else:
        items = payload
    observations = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or item.get("type") or "Strix candidate"
        subject = item.get("target") or item.get("location") or item.get("url") or "Strix target"
        severity = item.get("severity") or "unknown"
        observations.append(_observation("agent.candidate_finding", str(subject), f"{title} [{severity}]", .7, item))
    return observations


def _validate_source_path(source_path: str | None) -> Path | None:
    if not source_path:
        return None
    path = Path(source_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(422, "source_path 必须是存在的本地目录")
    return path


def _load_run(run_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import final_core
    with final_core.connect() as db:
        row = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Run 不存在")
    engagement = final_core.get_engagement(row["engagement_id"])
    if row["mode"] != "traditional" and not (row["mode"] == "web3" and engagement.get("target_type") == "repository"):
        raise HTTPException(409, "代码工具链必须绑定 Traditional 或 Web3 Repository Run")
    return dict(row), engagement


def record_coverage(run_id: str, surface_key: str, state: str, reason: str, observation_ids: list[str] | None = None) -> None:
    import final_core
    with final_core.connect() as db:
        db.execute(
            """INSERT INTO coverage_v2 VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(run_id,surface_key) DO UPDATE SET
               state=excluded.state,reason=excluded.reason,observation_ids=excluded.observation_ids,updated_at=excluded.updated_at""",
            (final_core.uid("coverage"), run_id, surface_key, state, reason, final_core.dump(observation_ids or []), final_core.utcnow()),
        )


def _persist_result(run: dict[str, Any], capability: str, envelope: capability_registry.ToolResultEnvelope, observations: list[dict[str, Any]], args: list[str]) -> dict[str, Any]:
    import final_core
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_id = final_core.uid("artifact")
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    safe_result = {
        "capability": envelope.capability, "status": envelope.status, "exit_code": envelope.exit_code,
        "stdout_sha256": hashlib.sha256(envelope.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(envelope.stderr.encode()).hexdigest(),
        "stderr_summary": redact(envelope.stderr[-1000:]), "synthetic": envelope.synthetic,
    }
    payload = {"tool_result": safe_result, "argv": [capability, *args], "observations": observations}
    artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    observation_ids = []
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, run["id"], f"traditional.tool.{capability}", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        for item in observations:
            observation_id = final_core.uid("obs")
            db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                observation_id, run["id"], run["engagement_id"], "traditional", item["observation_type"],
                item["subject"], item["summary"], item["confidence"], capability, artifact_id, final_core.utcnow(),
            ))
            observation_ids.append(observation_id)
    return {
        "capability": capability, "tool_result": safe_result, "artifact_id": artifact_id,
        "observation_ids": observation_ids, "observation_count": len(observation_ids),
        "subjects": [item["subject"] for item in observations],
    }


def run_capability(run_id: str, capability: str, source_path: str | None, timeout_seconds: int, target_override: str | None = None, scan_profile: str = "quick") -> dict[str, Any]:
    import final_core
    run, engagement = _load_run(run_id)
    if capability not in TRADITIONAL_CAPABILITIES:
        raise HTTPException(422, "未知 Traditional capability")
    source = _validate_source_path(source_path)
    scan_target = str(source) if capability == "strix" and source else (target_override or engagement["normalized_target"])
    if capability == "strix" and not strix_configured():
        raise HTTPException(409, "strix_provider_not_configured")
    if capability == "strix" and not strix_sandbox_ready():
        raise HTTPException(409, "strix_sandbox_not_ready")
    if capability == "shannon" and not shannon_ready():
        raise HTTPException(409, "shannon_provider_or_docker_not_ready")
    if capability in NETWORK_CAPABILITIES or capability == "shannon" or (capability == "strix" and not source):
        check = final_core.execution_policy_check(final_core.PolicyCheckInput(
            engagement_id=engagement["id"], target=scan_target, action="read",
            third_party_passive=capability == "subfinder",
        ))
        if not check["allowed"]:
            raise HTTPException(409, check["reason"])
        from traditional_runtime import ReplayRequest, network_guard
        network_guard(engagement, ReplayRequest(url=scan_target))
    consumed, reason = final_core.consume_run_budget(run_id, "tool_call", 1)
    if not consumed:
        raise HTTPException(409, reason)
    cwd = Path(__file__).resolve().parent if capability == "strix" else (source or Path(__file__).resolve().parent)
    snapshot_context = tempfile.TemporaryDirectory(prefix="strix-source-") if capability == "strix" and source else None
    try:
        if snapshot_context:
            snapshot = Path(snapshot_context.name) / "source"
            shutil.copytree(source, snapshot, ignore=shutil.ignore_patterns(".git", ".venv", "node_modules", "__pycache__", "strix_runs"))
            scan_target = str(snapshot)
        args = command_for(
            capability, scan_target, str(source) if source else None,
            int(float(engagement["policy"].get("max_requests_per_second", 1))),
            scan_profile, float(engagement["policy"].get("max_model_budget_usd", 1)),
        )
        started_at = time.time()
        envelope = capability_registry.execute(capability, args, cwd, timeout_seconds)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    except OSError as error:
        raise HTTPException(409, f"source_snapshot_failed: {redact(str(error))}") from error
    finally:
        if snapshot_context:
            snapshot_context.cleanup()
    observations = parse_output(capability, envelope.stdout, envelope.stderr)
    if capability == "strix":
        run_dir = latest_strix_run(cwd, started_at)
        structured = parse_strix_artifacts(run_dir) if run_dir else []
        observations = structured or observations
    return _persist_result(run, capability, envelope, observations, args)


@router.post("/runs/{run_id}/tools/run")
def run_traditional_tool(run_id: str, body: TraditionalToolInput):
    return run_capability(run_id, body.capability, body.source_path, body.timeout_seconds)


async def execute_toolchain(run_id: str, body: TraditionalToolchainInput, finalize: bool = True) -> None:
    import final_core
    run, _ = _load_run(run_id)
    engagement = final_core.get_engagement(run["engagement_id"])
    is_repository = engagement.get("target_type") == "repository"
    is_cidr = engagement.get("target_type") == "cidr"
    capabilities = (["httpx"] if body.include_recon and is_cidr else
                    list(NETWORK_CAPABILITIES if body.include_recon and not is_repository else ()))
    if body.include_code:
        capabilities.extend(CODE_CAPABILITIES)
    if body.include_strix:
        capabilities.append("strix")
    if body.include_shannon:
        capabilities.append("shannon")
    with final_core.connect() as db:
        db.execute("UPDATE analysis_runs SET status='running',synthetic=0,started_at=?,current_stage='surface' WHERE id=?", (final_core.utcnow(), run_id))
    final_core.add_event(run_id, "surface", "toolchain.started", "Traditional 工具链已启动", {"capabilities": capabilities, "synthetic": False})
    available = {item["id"]: item for item in capability_registry.inventory()}
    root_target = engagement["normalized_target"]
    discovered_hosts: list[str] = []
    responsive_urls: list[str] = []
    created_at = datetime.fromisoformat(run["created_at"]).timestamp()
    deadline = created_at + max(1, int(engagement["policy"].get("max_runtime_minutes", 30))) * 60
    for capability in capabilities:
        if not available.get(capability, {}).get("available"):
            final_core.add_event(run_id, "analysis", "capability.degraded", f"{capability} 未安装，本分支标记为 not-tested", {"capability": capability})
            record_coverage(run_id, f"capability:{capability}", "not_tested", "capability_unavailable")
            continue
        if capability == "httpx" and discovered_hosts:
            targets = [f"https://{host}" for host in discovered_hosts[:body.max_discovered_targets]]
        elif capability in {"katana", "nuclei"} and responsive_urls:
            targets = responsive_urls[:body.max_discovered_targets]
        else:
            targets = [root_target]
        for target in list(dict.fromkeys(targets)):
            remaining = int(deadline - time.time())
            if remaining < 5:
                with final_core.connect() as db:
                    db.execute("UPDATE analysis_runs SET status='timeout',stopped_at=? WHERE id=?", (final_core.utcnow(), run_id))
                final_core.add_event(run_id, "analysis", "run.timeout", "已达到总运行时间预算，剩余工具未执行", {"capability": capability})
                return
            with final_core.connect() as db:
                state = db.execute("SELECT status FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
                checkpoint_stage = f"tool.{capability}.{hashlib.sha256(target.encode()).hexdigest()[:12]}"
                completed = db.execute("SELECT 1 FROM checkpoints WHERE run_id=? AND stage=?", (run_id, checkpoint_stage)).fetchone()
            if not state or state["status"] in {"paused", "stopped", "budget_exhausted"}:
                return
            if completed:
                final_core.add_event(run_id, "analysis", "tool.skipped_checkpoint", f"{capability} 已有完成 checkpoint，恢复时跳过", {"capability": capability, "target": target})
                continue
            try:
                tool_timeout = min(remaining, max(body.timeout_seconds, 5400) if capability == "shannon" else body.timeout_seconds)
                result = await asyncio.to_thread(run_capability, run_id, capability, body.source_path, tool_timeout, target, body.scan_profile)
                final_core.add_event(run_id, "analysis", "tool.completed", f"{capability} 产生 {result['observation_count']} 条 Observation", {**result, "target": target})
                record_coverage(run_id, f"capability:{capability}:{target}", "tested", result["tool_result"]["status"], result["observation_ids"])
                if capability == "subfinder":
                    discovered_hosts.extend(subject for subject in result["subjects"] if "://" not in subject)
                elif capability == "httpx":
                    responsive_urls.extend(subject for subject in result["subjects"] if subject.startswith(("http://", "https://")))
                if result["tool_result"]["status"] == "completed":
                    with final_core.connect() as db:
                        db.execute("INSERT OR IGNORE INTO checkpoints VALUES(?,?,?,?,?)", (
                            final_core.uid("checkpoint"), run_id, checkpoint_stage, final_core.dump({"artifact_id": result["artifact_id"], "target": target}), final_core.utcnow(),
                        ))
            except HTTPException as error:
                policy_denials = {"out_of_scope", "third_party_passive_query_denied", "third_party_active_test_denied", "private_or_special_ip_denied"}
                kind = "tool.policy_denied" if any(str(error.detail).startswith(reason) for reason in policy_denials) else "tool.failed"
                final_core.add_event(run_id, "analysis", kind, f"{capability}: {error.detail}", {"capability": capability, "target": target})
                record_coverage(run_id, f"capability:{capability}:{target}", "not_tested", str(error.detail))
            except Exception as error:  # defensive boundary: one optional tool cannot forge overall success
                final_core.add_event(run_id, "analysis", "tool.failed", f"{capability}: {redact(str(error))}", {"capability": capability, "target": target})
                record_coverage(run_id, f"capability:{capability}:{target}", "not_tested", "adapter_exception")
    if body.include_native_agent and not is_repository:
        from native_agent import readiness as native_agent_readiness, run_native_agent
        agent = native_agent_readiness()
        if agent["ready"]:
            try:
                result = await asyncio.to_thread(run_native_agent, run_id)
                record_coverage(run_id, "capability:native-agent", "tested", "read_only_completed", result["observation_ids"])
            except Exception as error:
                final_core.add_event(run_id, "analysis", "native_agent.failed", f"Native Agent 降级：{redact(str(error))}")
                record_coverage(run_id, "capability:native-agent", "not_tested", "agent_runtime_failed")
        else:
            reason = "provider_not_configured" if agent["available"] else "browser_runtime_unavailable"
            final_core.add_event(run_id, "analysis", "native_agent.degraded", f"Native Agent 未运行：{reason}；确定性工具链继续完成", {"reason": reason})
            record_coverage(run_id, "capability:native-agent", "not_tested", reason)
    if not finalize:
        return
    try:
        correlation = final_core.correlate_observations(run_id)
    except HTTPException:
        correlation = {"count": 0, "created": []}
    with final_core.connect() as db:
        status = db.execute("SELECT status FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        if status and status["status"] == "running":
            db.execute("UPDATE analysis_runs SET status='completed',current_stage='verification',completed_at=? WHERE id=?", (final_core.utcnow(), run_id))
    final_core.add_event(run_id, "verification", "run.completed", f"真实工具链完成；生成 {correlation['count']} 个 Candidate，仍需独立 Oracle", {"synthetic": False, "candidate_count": correlation["count"]})


async def checkout_and_execute_repository(run_id: str, repository_url: str, body: TraditionalToolchainInput) -> None:
    import final_core
    parsed = urllib.parse.urlsplit(repository_url)
    clean_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if parsed.scheme != "https" or not parsed.netloc or len(parsed.path.strip("/").split("/")) < 2:
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='failed',stopped_at=? WHERE id=?", (final_core.utcnow(), run_id))
        final_core.add_event(run_id, "target", "repository.checkout_failed", "远程仓库地址不是有效的 HTTPS Git URL")
        return
    destination = REPOSITORY_ROOT / run_id
    REPOSITORY_ROOT.mkdir(parents=True, exist_ok=True)
    final_core.add_event(run_id, "target", "repository.checkout_started", f"正在检出授权仓库 {parsed.netloc}{parsed.path}", {"synthetic": False})
    try:
        result = await asyncio.to_thread(subprocess.run, ["git", "clone", "--depth", "1", "--", clean_url, str(destination)], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as error:
        result = None
        detail = "检出超时" if isinstance(error, subprocess.TimeoutExpired) else "Git 不可用"
    else:
        detail = redact((result.stderr or result.stdout)[-1000:])
    if not result or result.returncode != 0:
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='failed',stopped_at=? WHERE id=?", (final_core.utcnow(), run_id))
        final_core.add_event(run_id, "target", "repository.checkout_failed", detail or "仓库检出失败")
        return
    commit = await asyncio.to_thread(subprocess.run, ["git", "-C", str(destination), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15)
    final_core.add_event(run_id, "target", "repository.checkout_completed", f"仓库已检出，commit {commit.stdout.strip()[:12]}", {"source_path": str(destination), "synthetic": False})
    with final_core.connect() as db:
        row = db.execute("SELECT config FROM run_configs_v2 WHERE run_id=?", (run_id,)).fetchone()
        config = final_core.load(row["config"], {}) if row else {}
        config.update({"source_path": str(destination), "include_code": True, "include_recon": False})
        db.execute("UPDATE run_configs_v2 SET config=? WHERE run_id=?", (final_core.dump(config), run_id))
    with final_core.connect() as db:
        run_row = db.execute("SELECT engagement_id FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    engagement = final_core.get_engagement(run_row["engagement_id"])
    is_web3 = engagement.get("mode") == "web3"
    await execute_toolchain(
        run_id,
        body.model_copy(update={"source_path": str(destination), "include_code": True, "include_recon": False}),
        finalize=not is_web3,
    )
    if is_web3:
        from web3_analysis import execute_repository_pipeline
        try:
            await asyncio.to_thread(execute_repository_pipeline, engagement["id"], run_id, str(destination), commit.stdout.strip() or None)
            correlation = final_core.correlate_observations(run_id)
            with final_core.connect() as db:
                db.execute("UPDATE analysis_runs SET status='completed',current_stage='verification',completed_at=? WHERE id=? AND status='running'", (final_core.utcnow(), run_id))
            final_core.add_event(run_id, "verification", "run.completed", f"Web3 真实工具链完成；生成 {correlation['count']} 个 Candidate，仍需独立 Oracle", {"synthetic": False, "candidate_count": correlation["count"]})
        except Exception as error:
            with final_core.connect() as db:
                db.execute("UPDATE analysis_runs SET status='failed',stopped_at=? WHERE id=?", (final_core.utcnow(), run_id))
            final_core.add_event(run_id, "analysis", "web3.pipeline.failed", f"Web3 工具链失败：{redact(str(error))}")


async def execute_web3_source_pipeline(run_id: str, body: TraditionalToolchainInput, fork_rpc_url: str, fork_block_number: int | None = None, chain_id: int | None = None) -> None:
    """Run local code tools, create an isolated real-chain fork, then run Web3 analyzers."""
    import final_core
    from web3_analysis import execute_repository_pipeline
    from web3_lab import start_rpc_fork, stop_local_fork

    run, _ = _load_run(run_id)
    engagement = final_core.get_engagement(run["engagement_id"])
    fork_id = None
    try:
        fork = await asyncio.to_thread(start_rpc_fork, engagement["id"], fork_rpc_url, fork_block_number, chain_id)
        fork_id = fork["id"]
        final_core.add_event(run_id, "surface", "web3.fork.ready", f"本地 Fork 已就绪：chain {fork['chain_id']} / block {fork['fork_block']}", {"fork_id": fork_id, "chain_id": fork["chain_id"], "fork_block": fork["fork_block"]})
        await execute_toolchain(run_id, body.model_copy(update={"include_code": True, "include_recon": False, "include_native_agent": False}), finalize=False)
        await asyncio.to_thread(execute_repository_pipeline, engagement["id"], run_id, body.source_path)
        correlation = final_core.correlate_observations(run_id)
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='completed',current_stage='verification',completed_at=? WHERE id=? AND status='running'", (final_core.utcnow(), run_id))
        final_core.add_event(run_id, "verification", "run.completed", f"Web3 source + fork 工具链完成；生成 {correlation['count']} 个 Candidate，仍需独立 Oracle", {"synthetic": False, "candidate_count": correlation["count"], "fork_id": fork_id})
    except Exception as error:
        with final_core.connect() as db:
            db.execute("UPDATE analysis_runs SET status='failed',stopped_at=? WHERE id=?", (final_core.utcnow(), run_id))
        final_core.add_event(run_id, "analysis", "web3.pipeline.failed", f"Web3 source/fork 工具链失败：{redact(str(error))}")
    finally:
        if fork_id:
            try:
                stop_local_fork(fork_id)
            except HTTPException:
                pass


@router.post("/runs/{run_id}/toolchain", status_code=202)
async def start_traditional_toolchain(run_id: str, body: TraditionalToolchainInput):
    run, _ = _load_run(run_id)
    if run["status"] not in {"queued", "paused", "completed"}:
        raise HTTPException(409, "当前 Run 状态不能启动工具链")
    if body.include_code:
        _validate_source_path(body.source_path)
    asyncio.create_task(execute_toolchain(run_id, body))
    return {"id": run_id, "status": "queued", "synthetic": False, "capabilities": [*(NETWORK_CAPABILITIES if body.include_recon else ()), *(CODE_CAPABILITIES if body.include_code else ())]}
