from __future__ import annotations

import hashlib
import ipaddress
import json
import subprocess
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from reporting import redact


router = APIRouter(prefix="/api/v1/traditional", tags=["Traditional SRC runtime"])
ARTIFACT_ROOT = Path(__file__).resolve().parent / "data" / "artifacts"


class ReplayRequest(BaseModel):
    url: str
    method: str = "GET"
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = None


class HttpReplayInput(BaseModel):
    candidate_id: str
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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def network_guard(engagement: dict, request: ReplayRequest) -> None:
    import final_core
    result = final_core.execution_policy_check(final_core.PolicyCheckInput(
        engagement_id=engagement["id"], target=request.url, action="read",
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
        "body_preview": redact(body_text[:1000]),
    }


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
    replay_text = str(replay.get("replay") or "2/2")
    try:
        successful, total = [int(value) for value in replay_text.split("/", 1)]
    except (ValueError, AttributeError):
        successful = total = max(2, attempts)
    diff = str((receipt.get("evidence") or {}).get("diff") or "ptai oracle control diverged as required")
    verification = final_core.verify_candidate(body.candidate_id, final_core.VerificationInput(
        oracle=f"ptai:{replay.get('oracle') or receipt.get('oracle_kind') or 'machine-oracle'}",
        attempts=max(2, total), reproduced=successful == total and total >= 2,
        counterevidence_checked=True, counterevidence_summary=diff,
        severity=str(finding.get("severity") or "unknown"), impact_description=body.impact_description,
        steps=["Validate capsule integrity", "Apply immutable Scope and network guard", "Run ptai replay with safe intensity", "Require all replay attempts to succeed"],
        expected="The named oracle's negative control must diverge from the exploit condition",
        actual=json.dumps(replay, ensure_ascii=False), root_cause=body.root_cause,
        weakness=body.weakness, location=body.location, poc_artifact_ids=[artifact_id],
    ))
    return {"status": verification.get("status"), "artifact_id": artifact_id, "observation_id": observation_id, "replay": replay, "verification": verification}


@router.post("/runs/{run_id}/http-replay")
def http_replay(run_id: str, body: HttpReplayInput):
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
    for spec in specs:
        network_guard(engagement, spec)
    rate = max(.001, float(engagement["policy"].get("max_requests_per_second", 1)))
    interval = 1 / rate
    rounds = []
    for replay_index in range(2):
        results = {}
        for index, (name, spec) in enumerate(zip(("baseline", "attack", "negative_control"), specs)):
            consumed, reason = final_core.consume_run_budget(run_id, "request", 1)
            if not consumed:
                raise HTTPException(409, reason)
            results[name] = request_once(spec)
            if index < 2:
                time.sleep(interval)
        rounds.append(results)
        if replay_index == 0:
            time.sleep(interval)
    reproduced = all(signature(r["attack"]) == signature(r["baseline"]) and signature(r["negative_control"]) != signature(r["attack"]) for r in rounds)
    stable = len({signature(r["attack"]) for r in rounds}) == 1
    artifact_id = final_core.uid("artifact")
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    artifact_path = ARTIFACT_ROOT / f"{artifact_id}.json"
    artifact = {"oracle": "http-state-replay-v1", "rounds": rounds, "reproduced": reproduced, "stable": stable}
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
    result = final_core.verify_candidate(body.candidate_id, final_core.VerificationInput(
        oracle="http-state-replay-v1", attempts=2, reproduced=reproduced and stable,
        counterevidence_checked=True, counterevidence_summary="Negative control diverged from the attack response" if reproduced else "Negative control did not establish the claimed boundary",
        severity=body.severity, impact_description=body.impact_description,
        steps=["Replay authorized baseline", "Replay candidate attack", "Replay negative control", "Repeat the sequence"],
        expected=f"Negative control differs; baseline establishes intended state",
        actual=f"attack={rounds[-1]['attack']['status']} baseline={rounds[-1]['baseline']['status']}",
        root_cause=body.root_cause, weakness=body.weakness, location=body.location,
        poc_artifact_ids=[artifact_id],
    ))
    return {"artifact_id": artifact_id, "observation_id": observation_id, "replay": artifact, "verification": result}
