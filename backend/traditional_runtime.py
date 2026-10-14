from __future__ import annotations

import hashlib
import ipaddress
import json
import subprocess
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import difflib
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from reporting import redact, redact_structure


router = APIRouter(prefix="/api/v1/traditional", tags=["Traditional SRC runtime"])
ARTIFACT_ROOT = Path(__file__).resolve().parent / "data" / "artifacts"


class VerificationCancelled(Exception):
    pass


class ReplayRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class AuthorizationAssertion(BaseModel):
    baseline_identity: ReplayRequest
    attack_identity: ReplayRequest
    principal_field: str = Field(min_length=1, max_length=160)
    owner_field: str = Field(min_length=1, max_length=160)


class HttpReplayInput(BaseModel):
    candidate_id: str
    authorization: AuthorizationAssertion | None = None
    baseline: ReplayRequest
    attack: ReplayRequest
    negative_control: ReplayRequest
    severity: str
    impact_description: str
    root_cause: str
    weakness: str
    location: str


class PtaiReplayInput(BaseModel):
    candidate_id: str
    capsule: dict
    impact_description: str = Field(min_length=3, max_length=4000)
    root_cause: str = Field(min_length=2, max_length=2000)
    weakness: str = Field(min_length=2, max_length=160)
    location: str = Field(min_length=2, max_length=1000)
    timeout_seconds: int = Field(default=120, ge=10, le=600)


class ExchangeRequestInput(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=65536)
    identity_id: str | None = None


class ExchangeReplayInput(BaseModel):
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    body: str | None = Field(default=None, max_length=65536)
    identity_id: str | None = None


class ExchangeCandidateInput(BaseModel):
    title: str = Field(min_length=3, max_length=240)
    category: str = Field(min_length=2, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=4000)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def network_guard(engagement: dict, request: ReplayRequest) -> None:
    import final_core
    destructive = request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
    result = final_core.execution_policy_check(final_core.PolicyCheckInput(
        engagement_id=engagement["id"], target=request.url,
        action="analyze" if destructive else "read", destructive=destructive,
    ))
    if not result["allowed"]:
        raise HTTPException(409, result["reason"])
    host = urlparse(request.url).hostname
    if not host:
        raise HTTPException(422, "无效 HTTP URL")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise HTTPException(409, f"dns_resolution_failed: {error}") from error
    allow_private = bool(engagement["scope"].get("allow_private_ips", False))
    for address in addresses:
        ip = ipaddress.ip_address(address)
        unsafe = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        if unsafe and not allow_private:
            raise HTTPException(409, f"private_or_special_ip_denied: {address}")


def request_once(spec: ReplayRequest) -> dict:
    data = spec.body.encode() if spec.body is not None else None
    request = urllib.request.Request(spec.url, data=data, method=spec.method.upper(), headers=spec.headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=8) as response:
            body = response.read(65536)
            status, headers = response.status, dict(response.headers)
    except urllib.error.HTTPError as error:
        body = error.read(65536)
        status, headers = error.code, dict(error.headers)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise HTTPException(409, f"http_replay_failed: {error}") from error
    body_text = body.decode(errors="replace")
    return {
        "status": status, "body_sha256": hashlib.sha256(body).hexdigest(), "body_bytes": len(body),
        "headers": {k: redact(v) for k, v in headers.items() if k.lower() in {"content-type", "location", "etag"}},
        "body_preview": _safe_body_preview(body_text), "_transient_body": body_text,
    }


def _safe_body_preview(body_text: str) -> str:
    sensitive = {"authorization", "cookie", "password", "passwd", "secret", "token", "access_token", "refresh_token", "api_key", "apikey", "private_key"}
    try:
        value = json.loads(body_text)
    except json.JSONDecodeError:
        return redact(body_text[:1000])

    def clean(item):
        if isinstance(item, dict):
            return {key: "[REDACTED]" if str(key).lower() in sensitive or any(part in str(key).lower() for part in ("password", "secret", "token", "private_key")) else clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item[:100]]
        return item
    return redact(json.dumps(clean(value), ensure_ascii=False, separators=(",", ":"))[:1000])


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    sensitive = {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key"}
    return {
        str(key): "[REDACTED]" if str(key).lower() in sensitive else redact(str(item))
        for key, item in headers.items()
    }


def resolve_identity_headers(identity_id: str) -> dict[str, str]:
    """Resolve an opaque macOS Keychain reference only for the live request."""
    import final_core
    with final_core.connect() as db:
        row = db.execute("""SELECT i.engagement_id,i.credential_ref,p.auth_type,p.session_status
          FROM identities i JOIN identity_profiles p ON p.identity_id=i.id WHERE i.id=?""", (identity_id,)).fetchone()
    if not row:
        raise HTTPException(404, "测试身份不存在")
    if row["session_status"] != "ready":
        raise HTTPException(409, "测试身份会话未就绪，请先刷新登录态")
    reference = row["credential_ref"] or ""
    if not reference.startswith("keychain://"):
        if row["auth_type"] == "none":
            return {}
        raise HTTPException(409, "真实跨身份测试要求 macOS Keychain 引用")
    parts = reference.removeprefix("keychain://").split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise HTTPException(422, "Keychain 引用格式应为 keychain://service/account")
    service, account = parts
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HTTPException(409, f"keychain_session_unavailable: {type(error).__name__}") from error
    if result.returncode != 0:
        raise HTTPException(409, "Keychain 中找不到该测试会话，请重新采集登录态")
    try:
        secret = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise HTTPException(409, "Keychain 会话必须是包含 headers 对象的 JSON") from error
    headers = secret.get("headers") if isinstance(secret, dict) else None
    if not isinstance(headers, dict) or not headers:
        raise HTTPException(409, "Keychain 会话缺少 headers 对象")
    clean = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str) or len(key) > 120 or len(value) > 16384:
            raise HTTPException(409, "Keychain 会话 Header 格式不合法")
        if any(char in key + value for char in ("\r", "\n")):
            raise HTTPException(409, "Keychain 会话 Header 包含非法换行")
        clean[key] = value
    return clean


def _get_exchange(exchange_id: str) -> dict:
    import final_core
    with final_core.connect() as db:
        row = db.execute("SELECT * FROM http_exchanges WHERE id=?", (exchange_id,)).fetchone()
    if not row:
        raise HTTPException(404, "HTTP Exchange 不存在")
    value = dict(row)
    value["request_headers"] = final_core.load(value["request_headers"], {})
    value["response_headers"] = final_core.load(value["response_headers"], {})
    return value


def _record_exchange(run: dict, spec: ReplayRequest, result: dict, identity_id: str | None, source: str, parent_exchange_id: str | None = None) -> dict:
    import final_core
    if identity_id:
        identity = final_core.get_identity(identity_id)
        if identity["engagement_id"] != run["engagement_id"]:
            raise HTTPException(409, "测试身份不属于当前项目")
        if identity["session_status"] != "ready":
            raise HTTPException(409, "测试身份会话未就绪，请先刷新登录态")
    exchange_id = final_core.uid("http")
    with final_core.connect() as db:
        db.execute("INSERT INTO http_exchanges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            exchange_id, run["id"], run["engagement_id"], identity_id,
            spec.method.upper(), spec.url, final_core.dump(_safe_headers(spec.headers)),
            redact(spec.body) if spec.body else None, int(result["status"]),
            final_core.dump(result["headers"]), result["body_preview"], result["body_sha256"],
            int(result["body_bytes"]), source, parent_exchange_id, final_core.utcnow(),
        ))
    return _get_exchange(exchange_id)


def _execute_exchange(run_id: str, body: ExchangeRequestInput, source: str, parent_exchange_id: str | None = None, include_transient: bool = False) -> dict:
    import final_core
    method = body.method.upper()
    if method not in {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}:
        raise HTTPException(422, "不支持的 HTTP 方法")
    with final_core.connect() as db:
        run_row = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
    if not run_row or run_row["mode"] != "traditional":
        raise HTTPException(409, "HTTP 工作台必须绑定 Traditional Run")
    run = dict(run_row)
    if run["status"] not in {"running", "paused", "completed"}:
        raise HTTPException(409, "当前 Run 状态不允许 HTTP 研究操作")
    engagement = final_core.get_engagement(run["engagement_id"])
    identity_headers = resolve_identity_headers(body.identity_id) if body.identity_id else {}
    spec = ReplayRequest(url=body.url, method=method, headers={**body.headers, **identity_headers}, body=body.body)
    network_guard(engagement, spec)
    consumed, reason = final_core.consume_run_budget(run_id, "request", 1)
    if not consumed:
        raise HTTPException(409, reason)
    result = request_once(spec)
    exchange = _record_exchange(run, spec, result, body.identity_id, source, parent_exchange_id)
    if include_transient:
        exchange["_transient_body"] = result.get("_transient_body", "")
    final_core.add_event(run_id, "verification", "http.exchange_recorded", f"{method} {urlparse(body.url).path or '/'} → {result['status']}", {"exchange_id": exchange["id"], "source": source})
    return exchange


@router.get("/runs/{run_id}/http-exchanges")
def list_http_exchanges(run_id: str):
    import final_core
    with final_core.connect() as db:
        if not db.execute("SELECT 1 FROM analysis_runs WHERE id=?", (run_id,)).fetchone():
            raise HTTPException(404, "Run 不存在")
        ids = [row["id"] for row in db.execute("SELECT id FROM http_exchanges WHERE run_id=? ORDER BY created_at DESC", (run_id,))]
    return [_get_exchange(exchange_id) for exchange_id in ids]


@router.post("/runs/{run_id}/http-exchanges", status_code=201)
def create_http_exchange(run_id: str, body: ExchangeRequestInput):
    return _execute_exchange(run_id, body, "manual")


@router.post("/http-exchanges/{exchange_id}/replay", status_code=201)
def replay_http_exchange(exchange_id: str, body: ExchangeReplayInput):
    original = _get_exchange(exchange_id)
    replay = _execute_exchange(original["run_id"], ExchangeRequestInput(
        url=body.url or original["url"], method=body.method or original["method"],
        headers=body.headers if body.headers is not None else original["request_headers"],
        body=body.body if body.body is not None else original["request_body"],
        identity_id=body.identity_id if body.identity_id is not None else original["identity_id"],
    ), "replay", exchange_id)
    before, after = original["response_body_preview"].splitlines(), replay["response_body_preview"].splitlines()
    replay["diff"] = {
        "status_changed": original["response_status"] != replay["response_status"],
        "body_changed": original["response_sha256"] != replay["response_sha256"],
        "bytes_delta": replay["response_bytes"] - original["response_bytes"],
        "preview": "\n".join(difflib.unified_diff(before, after, fromfile=original["id"], tofile=replay["id"], lineterm=""))[:8000],
    }
    return replay


@router.post("/http-exchanges/{exchange_id}/candidate", status_code=201)
def exchange_to_candidate(exchange_id: str, body: ExchangeCandidateInput):
    import final_core
    exchange = _get_exchange(exchange_id)
    observation = final_core.record_observation(exchange["run_id"], final_core.ObservationInput(
        observation_type="http.exchange", subject=f"{exchange['method']} {exchange['url']}",
        summary=f"HTTP {exchange['response_status']} · sha256 {exchange['response_sha256'][:12]}",
        source_capability="http-workbench", confidence=.65, raw_ref=exchange["id"],
    ))
    candidate = final_core.create_candidate(exchange["run_id"], final_core.CandidateInput(
        title=body.title, category=body.category, target=exchange["url"],
        hypothesis=body.hypothesis, observation_ids=[observation["id"]],
    ))
    return {"exchange_id": exchange_id, "observation": observation, "candidate": candidate}


def signature(result: dict) -> tuple[int, str]:
    return result["status"], result["body_sha256"]


def capsule_integrity(capsule: dict) -> str:
    body = {key: value for key, value in capsule.items() if key != "integrity_sha256"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def safe_capsule(capsule: dict) -> dict:
    value = json.loads(redact(json.dumps(capsule, ensure_ascii=False)))
    finding = value.get("finding") if isinstance(value.get("finding"), dict) else {}
    finding.pop("evidence", None)
    receipt = value.get("receipt") if isinstance(value.get("receipt"), dict) else {}
    evidence = receipt.get("evidence") if isinstance(receipt.get("evidence"), dict) else {}
    evidence.pop("request", None); evidence.pop("response", None)
    return value


@router.post("/runs/{run_id}/ptai-replay")
def ptai_replay(run_id: str, body: PtaiReplayInput):
    import capability_registry
    import final_core
    encoded = json.dumps(body.capsule, ensure_ascii=False)
    if len(encoded.encode()) > 1_000_000:
        raise HTTPException(413, "ptai capsule 超过 1MB 限制")
    with final_core.connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=? AND run_id=?", (body.candidate_id, run_id)).fetchone()
    if not run or run["mode"] != "traditional" or not candidate:
        raise HTTPException(409, "ptai replay 必须绑定当前 Traditional Run 的 Candidate")
    capsule = body.capsule
    if capsule.get("capsule_schema_version") != 1 or capsule.get("integrity_sha256") != capsule_integrity(capsule):
        raise HTTPException(422, "ptai capsule integrity 校验失败")
    finding = capsule.get("finding") if isinstance(capsule.get("finding"), dict) else {}
    receipt = capsule.get("receipt") if isinstance(capsule.get("receipt"), dict) else {}
    target = str(finding.get("target") or "")
    if receipt.get("verdict") != "verified" or not finding.get("verification_recipe"):
        raise HTTPException(422, "胶囊必须包含 verified receipt 和可重放 recipe")
    engagement = final_core.get_engagement(run["engagement_id"])
    network_guard(engagement, ReplayRequest(url=target))
    attempts = int((receipt.get("replay") or {}).get("attempts") or 2)
    for _ in range(max(2, min(attempts, 20))):
        consumed, reason = final_core.consume_run_budget(run_id, "request", 1)
        if not consumed:
            raise HTTPException(409, reason)
    consumed, reason = final_core.consume_run_budget(run_id, "tool_call", 1)
    if not consumed:
        raise HTTPException(409, reason)
    executable = capability_registry.resolve_executable("ptai")
    if not executable:
        raise HTTPException(409, "ptai capability unavailable")
    with tempfile.NamedTemporaryFile(mode="w", suffix="-ptai-capsule.json", encoding="utf-8") as handle:
        handle.write(encoded); handle.flush()
        try:
            process = subprocess.run(
                [executable, "replay", handle.name, "--intensity", "safe", "--ci"],
                capture_output=True, text=True, timeout=body.timeout_seconds, shell=False,
            )
        except subprocess.TimeoutExpired as error:
            raise HTTPException(409, "ptai replay timeout") from error
    try:
        replay = json.loads(process.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        replay = {"integrity_ok": False, "verdict": "candidate", "error": redact(process.stderr[-1000:])}
    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    artifact = {"capsule": safe_capsule(capsule), "replay": replay, "exit_code": process.returncode}
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    reproduced = process.returncode == 0 and replay.get("integrity_ok") is True and replay.get("verdict") == "verified"
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, run_id, "ptai.proof_capsule_replay", str(artifact_path),
            hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run_id, run["engagement_id"], "traditional", "verification.ptai_replay",
            target, f"ptai safe replay verdict={replay.get('verdict', 'candidate')}",
            1.0 if reproduced else .3, "pentest-ai", artifact_id, final_core.utcnow(),
        ))
    if not reproduced:
        return {"status": "human_review", "artifact_id": artifact_id, "observation_id": observation_id, "replay": replay}
    replay_text = str(replay.get("replay") or "")
    try:
        successful, total = [int(value) for value in replay_text.split("/", 1)]
    except (ValueError, AttributeError):
        raise HTTPException(409, "ptai 未提供可验证的逐轮成功计数")
    diff = str((receipt.get("evidence") or {}).get("diff") or "ptai oracle control diverged as required")
    proof = final_core.VerificationInput(
        oracle=f"ptai:{replay.get('oracle') or receipt.get('oracle_kind') or 'machine-oracle'}",
        attempts=max(2, total), reproduced=successful == total and total >= 2,
        counterevidence_checked=True, counterevidence_summary=diff,
        severity=str(finding.get("severity") or "unknown"), impact_description=body.impact_description,
        steps=["Validate capsule integrity", "Apply immutable Scope and network guard", "Run ptai replay with safe intensity", "Require all replay attempts to succeed"],
        expected="The named oracle's negative control must diverge from the exploit condition",
        actual=json.dumps(replay, ensure_ascii=False), root_cause=body.root_cause,
        weakness=body.weakness, location=body.location, poc_artifact_ids=[artifact_id],
    )
    from verification_receipts import issue_receipt
    proof.receipt_id = issue_receipt(body.candidate_id, proof)
    verification = final_core.verify_candidate(body.candidate_id, proof)
    return {"status": verification.get("status"), "artifact_id": artifact_id, "observation_id": observation_id, "replay": replay, "verification": verification}


def json_scalar(response: dict, path: str):
    try:
        value = json.loads(response.get("_transient_body", ""))
        for part in path.split("."):
            value = value[part]
        return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) else None
    except (ValueError, TypeError, KeyError):
        return None


def authorization_round(results: dict, assertion: AuthorizationAssertion) -> dict:
    owner = json_scalar(results["baseline_identity"], assertion.principal_field)
    other = json_scalar(results["attack_identity"], assertion.principal_field)
    baseline_owner = json_scalar(results["baseline"], assertion.owner_field)
    attack_owner = json_scalar(results["attack"], assertion.owner_field)
    checks = {
        "identities_authenticated": all(results[name]["status"] == 200 for name in ("baseline_identity", "attack_identity")),
        "distinct_principals": owner is not None and other is not None and owner != other,
        "owner_binding": owner is not None and baseline_owner == owner and attack_owner == owner,
        "object_read": results["baseline"]["status"] == 200 and results["attack"]["status"] == 200,
        "same_object_response": signature(results["baseline"]) == signature(results["attack"]),
        "unauthenticated_denied": results["negative_control"]["status"] in {401, 403},
    }
    return {"passed": all(checks.values()), "checks": checks}


def authorization_repair_round(results: dict, assertion: AuthorizationAssertion) -> dict:
    owner = json_scalar(results["baseline_identity"], assertion.principal_field)
    other = json_scalar(results["attack_identity"], assertion.principal_field)
    baseline_owner = json_scalar(results["baseline"], assertion.owner_field)
    checks = {
        "identities_authenticated": all(results[name]["status"] == 200 for name in ("baseline_identity", "attack_identity")),
        "distinct_principals": owner is not None and other is not None and owner != other,
        "baseline_owner_binding": owner is not None and baseline_owner == owner and results["baseline"]["status"] == 200,
        "non_owner_denied": results["attack"]["status"] in {401, 403},
        "unauthenticated_denied": results["negative_control"]["status"] in {401, 403},
    }
    return {"passed": all(checks.values()), "checks": checks}


def prepare_http_replay(run_id: str, body: HttpReplayInput):
    import final_core
    with final_core.connect() as db:
        run = db.execute("SELECT * FROM analysis_runs WHERE id=?", (run_id,)).fetchone()
        candidate = db.execute("SELECT * FROM candidate_findings WHERE id=? AND run_id=?", (body.candidate_id, run_id)).fetchone()
    if not run or run["mode"] != "traditional":
        raise HTTPException(409, "HTTP replay 必须绑定 Traditional Run")
    if not candidate:
        raise HTTPException(409, "Candidate 不存在或不属于当前 Run")
    engagement = final_core.get_engagement(run["engagement_id"])
    specs = [body.baseline, body.attack, body.negative_control]
    names = ["baseline", "attack", "negative_control"]
    if body.authorization:
        assertion = body.authorization
        if any(spec.method.upper() != "GET" or spec.body is not None for spec in [*specs, assertion.baseline_identity, assertion.attack_identity]):
            raise HTTPException(422, "对象读取 Oracle 仅支持无请求体的 GET")
        if body.baseline.url != body.attack.url or body.attack.url != candidate["target"] or body.negative_control.url != body.attack.url:
            raise HTTPException(422, "基线、测试、未登录负对照必须访问候选的同一对象 URL")
        if body.baseline.headers == body.attack.headers or not body.baseline.headers or not body.attack.headers:
            raise HTTPException(422, "必须使用两个不同的已登录身份")
        if body.negative_control.headers:
            raise HTTPException(422, "负对照必须不携带身份请求头")
        if assertion.baseline_identity.headers != body.baseline.headers or assertion.attack_identity.headers != body.attack.headers:
            raise HTTPException(422, "身份检查必须与对应对象请求使用相同凭据")
        if assertion.baseline_identity.url != assertion.attack_identity.url:
            raise HTTPException(422, "两个身份必须使用同一个身份检查端点")
        specs.extend([assertion.baseline_identity, assertion.attack_identity])
        names.extend(["baseline_identity", "attack_identity"])
    for spec in specs:
        network_guard(engagement, spec)
    return run, engagement, specs, names


@router.post("/runs/{run_id}/http-replay/plan")
def preview_http_replay(run_id: str, body: HttpReplayInput):
    _, engagement, specs, names = prepare_http_replay(run_id, body)
    return {"oracle": "http-authorization-read-v2" if body.authorization else "http-state-replay-v1",
            "rounds": 2, "request_count": len(specs) * 2,
            "requests": [{"role": name, "method": spec.method, "url": redact(spec.url)} for name, spec in zip(names, specs)],
            "max_requests_per_second": engagement["policy"].get("max_requests_per_second", 1),
            "can_prove": bool(body.authorization), "sends_requests": False,
            "checks": ["两个已登录且不同的主体", "基线对象归属与主体一致", "另一主体读取同一对象", "未登录访问被拒绝", "两轮结果稳定"],
            "limitation": "适用于 JSON 对象读取与身份归属检查；不支持写入、非 JSON 响应或通用业务授权推断。"}


@router.post("/runs/{run_id}/http-replay")
def http_replay(run_id: str, body: HttpReplayInput):
    return execute_http_replay(run_id, body)


def _job_update(job_id: str, *, status: str | None = None, phase: str | None = None,
                completed_requests: int | None = None, result: dict | None = None,
                error: str | None = None, completed: bool = False) -> None:
    import final_core
    assignments, values = [], []
    for column, value in (("status", status), ("phase", phase),
                          ("completed_requests", completed_requests),
                          ("result", final_core.dump(result) if result is not None else None),
                          ("error", redact(error) if error is not None else None)):
        if value is not None:
            assignments.append(f"{column}=?")
            values.append(value)
    if completed:
        assignments.append("completed_at=?")
        values.append(final_core.utcnow())
    if not assignments:
        return
    values.append(job_id)
    with final_core.connect() as db:
        db.execute(f"UPDATE verification_jobs SET {','.join(assignments)} WHERE id=?", values)


def _job_cancel_requested(job_id: str) -> bool:
    import final_core
    with final_core.connect() as db:
        row = db.execute("SELECT cancel_requested FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    return bool(row and row["cancel_requested"])


def execute_http_replay(run_id: str, body: HttpReplayInput, job_id: str | None = None):
    import final_core
    run, engagement, specs, names = prepare_http_replay(run_id, body)
    rate = max(.001, float(engagement["policy"].get("max_requests_per_second", 1)))
    interval = 1 / rate
    rounds = []
    completed_requests = 0
    for replay_index in range(2):
        results = {}
        for index, (name, spec) in enumerate(zip(names, specs)):
            if job_id and _job_cancel_requested(job_id):
                raise VerificationCancelled("用户取消了复验")
            if job_id:
                _job_update(job_id, phase=f"第 {replay_index + 1} 轮 · {name}")
            consumed, reason = final_core.consume_run_budget(run_id, "request", 1)
            if not consumed:
                raise HTTPException(409, reason)
            results[name] = request_once(spec)
            completed_requests += 1
            if job_id:
                _job_update(job_id, completed_requests=completed_requests)
            if index < len(specs) - 1:
                time.sleep(interval)
        rounds.append(results)
        if replay_index == 0:
            time.sleep(interval)
    differential_match = all(signature(r["attack"]) == signature(r["baseline"]) and signature(r["negative_control"]) != signature(r["attack"]) for r in rounds)
    if job_id and _job_cancel_requested(job_id):
        raise VerificationCancelled("用户取消了复验")
    stable = len({signature(r["attack"]) for r in rounds}) == 1
    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    semantic_checks = [authorization_round(r, body.authorization) for r in rounds] if body.authorization else []
    repair_checks = [authorization_repair_round(r, body.authorization) for r in rounds] if body.authorization else []
    reproduced = bool(semantic_checks) and all(check["passed"] for check in semantic_checks)
    repaired = bool(repair_checks) and all(check["passed"] for check in repair_checks)
    repaired = repaired and len({signature(r["attack"]) for r in rounds}) == 1
    oracle = "http-authorization-read-v2" if body.authorization else "http-state-replay-v1"
    for results in rounds:
        for response in results.values():
            response.pop("_transient_body", None)
    artifact = {"oracle": oracle, "rounds": rounds, "reproduced": reproduced, "repaired": repaired, "stable": stable,
                "differential_match": differential_match, "semantic_checks": semantic_checks,
                "repair_checks": repair_checks,
                "assertion": {"principal_field": body.authorization.principal_field, "owner_field": body.authorization.owner_field} if body.authorization else None,
                "limitation": "仅证明所选身份与对象归属字段的读取边界；业务授权规则与影响仍需审阅。"}
    artifact_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2))
    with final_core.connect() as db:
        db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (
            artifact_id, run_id, "http.replay", str(artifact_path), hashlib.sha256(artifact_path.read_bytes()).hexdigest(), "application/json", 1, final_core.utcnow(),
        ))
        observation_id = final_core.uid("obs")
        db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            observation_id, run_id, run["engagement_id"], "traditional", "http.replay_result",
            body.attack.url, f"Two-round HTTP replay reproduced={reproduced} stable={stable}",
            1.0 if reproduced and stable else .3, "http-state-replay", artifact_id, final_core.utcnow(),
        ))
    proof = final_core.VerificationInput(
        oracle=oracle, attempts=2, reproduced=reproduced and stable,
        counterevidence_checked=True, counterevidence_summary="Unauthenticated access denied; authenticated distinct principal read owner-bound object in both rounds" if reproduced else "Identity/object ownership assertions missing or not established; response differences alone are not proof",
        severity=body.severity, impact_description=body.impact_description,
        steps=["Replay authorized baseline", "Replay candidate attack", "Replay negative control", "Repeat the sequence"],
        expected="Authenticated non-owner cannot read owner-bound object; unauthenticated control is denied",
        actual=f"attack={rounds[-1]['attack']['status']} baseline={rounds[-1]['baseline']['status']}",
        root_cause=body.root_cause, weakness=body.weakness, location=body.location,
        poc_artifact_ids=[artifact_id],
    )
    if proof.reproduced:
        from verification_receipts import issue_receipt
        proof.receipt_id = issue_receipt(body.candidate_id, proof)
        result = final_core.verify_candidate(body.candidate_id, proof)
    elif repaired:
        from verification_receipts import issue_fixed_receipt
        result = issue_fixed_receipt(body.candidate_id, oracle, artifact_id, repair_checks)
        final_core.add_event(run_id, "verification", "finding.verified_fixed",
                             "两轮身份边界负向复测均拒绝旧攻击，Finding 已确认修复",
                             {"finding_id": result["id"], "candidate_id": body.candidate_id,
                              "receipt_id": result["receipt_id"]})
    else:
        result = final_core.verify_candidate(body.candidate_id, proof)
    return {"artifact_id": artifact_id, "observation_id": observation_id, "replay": artifact, "verification": result}


def _run_http_replay_job(job_id: str, run_id: str, body: HttpReplayInput) -> None:
    import final_core
    _job_update(job_id, status="running", phase="准备身份与范围检查")
    with final_core.connect() as db:
        db.execute("UPDATE verification_jobs SET started_at=? WHERE id=?", (final_core.utcnow(), job_id))
    try:
        result = execute_http_replay(run_id, body, job_id)
    except VerificationCancelled as error:
        _job_update(job_id, status="cancelled", phase="已取消", error=str(error), completed=True)
    except HTTPException as error:
        _job_update(job_id, status="failed", phase="执行失败", error=str(error.detail), completed=True)
    except Exception as error:
        _job_update(job_id, status="failed", phase="执行失败", error=f"{type(error).__name__}: {error}", completed=True)
    else:
        _job_update(job_id, status="completed", phase="复验完成", result=result, completed=True)


def _hydrate_verification_job(row) -> dict:
    import final_core
    value = dict(row)
    value["cancel_requested"] = bool(value["cancel_requested"])
    value["result"] = final_core.load(value["result"], None)
    value["progress"] = round(value["completed_requests"] / value["total_requests"], 3) if value["total_requests"] else 0
    return redact_structure(value)


@router.post("/runs/{run_id}/http-replay/jobs", status_code=202)
def start_http_replay_job(run_id: str, body: HttpReplayInput):
    import final_core
    _, _, specs, _ = prepare_http_replay(run_id, body)
    if not body.authorization:
        raise HTTPException(422, "后台机器复验必须配置身份与对象归属断言")
    with final_core.connect() as db:
        active = db.execute("""SELECT id FROM verification_jobs
            WHERE candidate_id=? AND status IN ('queued','running','cancelling')""", (body.candidate_id,)).fetchone()
        if active:
            raise HTTPException(409, f"这条候选已有进行中的复验：{active['id']}")
        job_id, timestamp = final_core.uid("verify-job"), final_core.utcnow()
        db.execute("INSERT INTO verification_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            job_id, body.candidate_id, run_id, "http-authorization-read-v2", "queued", "等待执行",
            0, len(specs) * 2, 0, None, None, timestamp, None, None,
        ))
    thread = threading.Thread(target=_run_http_replay_job, args=(job_id, run_id, body), daemon=True,
                              name=f"fieldwork-{job_id}")
    thread.start()
    return get_http_replay_job(job_id)


@router.get("/http-replay/jobs/{job_id}")
def get_http_replay_job(job_id: str):
    import final_core
    with final_core.connect() as db:
        row = db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, "复验任务不存在")
    return _hydrate_verification_job(row)


@router.post("/http-replay/jobs/{job_id}/cancel")
def cancel_http_replay_job(job_id: str):
    import final_core
    with final_core.connect() as db:
        row = db.execute("SELECT * FROM verification_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, "复验任务不存在")
        if row["status"] not in {"queued", "running", "cancelling"}:
            raise HTTPException(409, "复验任务已经结束")
        db.execute("UPDATE verification_jobs SET cancel_requested=1,status='cancelling',phase='等待当前请求结束' WHERE id=?", (job_id,))
    return get_http_replay_job(job_id)
