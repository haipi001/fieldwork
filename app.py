from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from adapters import adapter_statuses, build_command, stop_process, stream_process
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from final_core import init_final_db, mark_campaign_scheduler, router as final_router, run_due_campaign_schedules
from web3_lab import router as web3_router, shutdown_labs
from web3_analysis import router as web3_analysis_router
from web3_practice import router as web3_practice_router
from traditional_runtime import router as traditional_router
from traditional_tools import router as traditional_tools_router
from lifecycle import finalize_database_version, prepare_database_upgrade
from version import APP_VERSION, BUILD_NUMBER, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "src_control.db"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db() -> None:
    DATA.mkdir(exist_ok=True)
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS engagements (
          id TEXT PRIMARY KEY, program TEXT NOT NULL, status TEXT NOT NULL,
          manifest TEXT NOT NULL, confirmed_at TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
          id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, status TEXT NOT NULL,
          requests_used INTEGER NOT NULL DEFAULT 0, request_limit INTEGER NOT NULL,
          started_at TEXT, stopped_at TEXT, FOREIGN KEY(engagement_id) REFERENCES engagements(id)
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL,
          message TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS findings (
          id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, title TEXT NOT NULL,
          severity TEXT NOT NULL, status TEXT NOT NULL, target TEXT NOT NULL,
          evidence TEXT NOT NULL, counterevidence TEXT NOT NULL,
          replay_count INTEGER NOT NULL DEFAULT 0, oracle TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coverage (
          id INTEGER PRIMARY KEY AUTOINCREMENT, engagement_id TEXT NOT NULL,
          surface TEXT NOT NULL, state TEXT NOT NULL, note TEXT NOT NULL,
          updated_at TEXT NOT NULL, UNIQUE(engagement_id, surface)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, engagement_id TEXT,
          action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS executions (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL, adapter TEXT NOT NULL,
          executable TEXT NOT NULL, status TEXT NOT NULL, exit_code INTEGER,
          started_at TEXT NOT NULL, stopped_at TEXT
        );
        CREATE TABLE IF NOT EXISTS submissions (
          id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, platform TEXT NOT NULL,
          status TEXT NOT NULL, report TEXT NOT NULL, checklist TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS surfaces (
          id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, url TEXT NOT NULL,
          method TEXT NOT NULL, role TEXT NOT NULL, object_type TEXT NOT NULL,
          source TEXT NOT NULL, state TEXT NOT NULL, discovered_at TEXT NOT NULL,
          UNIQUE(engagement_id,url,method,role)
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
          id TEXT PRIMARY KEY, engagement_id TEXT NOT NULL, surface_id TEXT,
          category TEXT NOT NULL, statement TEXT NOT NULL, status TEXT NOT NULL,
          finding_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vulnerability_memory (
          fingerprint TEXT PRIMARY KEY, category TEXT NOT NULL, normalized_target TEXT NOT NULL,
          title TEXT NOT NULL, latest_finding_id TEXT NOT NULL, occurrences INTEGER NOT NULL,
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS evidence_ledger (
          id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, kind TEXT NOT NULL,
          content TEXT NOT NULL, sha256 TEXT NOT NULL, source TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(finding_id,kind)
        );
        CREATE TABLE IF NOT EXISTS counterevidence_ledger (
          id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, kind TEXT NOT NULL,
          content TEXT NOT NULL, sha256 TEXT NOT NULL, source TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(finding_id,kind)
        );
        CREATE TABLE IF NOT EXISTS dns_pins (
          engagement_id TEXT NOT NULL, host TEXT NOT NULL, addresses TEXT NOT NULL,
          first_seen_at TEXT NOT NULL, last_checked_at TEXT NOT NULL,
          PRIMARY KEY(engagement_id,host)
        );
        CREATE TABLE IF NOT EXISTS budgets (
          run_id TEXT PRIMARY KEY, request_limit INTEGER NOT NULL, requests_used INTEGER NOT NULL,
          tool_call_limit INTEGER NOT NULL, tool_calls_used INTEGER NOT NULL,
          model_budget_micros INTEGER NOT NULL, model_cost_micros INTEGER NOT NULL,
          updated_at TEXT NOT NULL
        );
        """)
        run_columns = {row["name"] for row in db.execute("PRAGMA table_info(runs)")}
        if "adapter" not in run_columns:
            db.execute("ALTER TABLE runs ADD COLUMN adapter TEXT NOT NULL DEFAULT 'demo'")
        for run in db.execute("SELECT * FROM runs").fetchall():
            db.execute("""INSERT OR IGNORE INTO budgets VALUES(?,?,?,?,?,?,?,?)""", (
                run["id"], run["request_limit"], run["requests_used"], 200, 0, 10_000_000, 0, now()))
        completed = db.execute("SELECT DISTINCT engagement_id FROM runs WHERE status='completed'").fetchall()
        for row in completed:
            eid = row["engagement_id"]
            if db.execute("SELECT 1 FROM coverage WHERE engagement_id=?", (eid,)).fetchone():
                continue
            db.executemany("INSERT INTO coverage(engagement_id,surface,state,note,updated_at) VALUES(?,?,?,?,?)", [
                (eid, "身份认证与会话", "tested", "已执行登录态与会话差异检查", now()),
                (eid, "对象访问控制", "open_proof_gap", "发现响应差异，等待独立复验", now()),
                (eid, "输入与注入", "not_tested", "本轮未发现需要启动该检查器的输入面", now()),
                (eid, "业务逻辑", "not_tested", "需要测试账号与业务状态机后再测试", now()),
            ])
        # A local process restart must never leave a task looking live. The next
        # explicit user start creates a new run while preserving the old trace.
        interrupted = db.execute("SELECT id,engagement_id FROM runs WHERE status IN ('queued','running')").fetchall()
        for row in interrupted:
            db.execute("UPDATE runs SET status='interrupted',stopped_at=? WHERE id=?", (now(), row["id"]))
            db.execute("INSERT INTO audit_log(engagement_id,action,detail,created_at) VALUES(?,?,?,?)",
                       (row["engagement_id"], "run.interrupted", f"{row['id']} 因本地进程重启而失败关闭", now()))
        existing_findings = db.execute("SELECT * FROM findings").fetchall()
        for finding in existing_findings:
            fingerprint = finding_fingerprint("access_control", finding["target"], finding["title"])
            db.execute("""INSERT INTO vulnerability_memory VALUES(?,?,?,?,?,?,?,?)
              ON CONFLICT(fingerprint) DO NOTHING""", (
                fingerprint, "access_control", normalize_target(finding["target"]), finding["title"],
                finding["id"], 1, finding["created_at"], finding["created_at"],
            ))
            if not db.execute("SELECT 1 FROM evidence_ledger WHERE finding_id=?", (finding["id"],)).fetchone():
                for kind, content in json.loads(finding["evidence"] or "{}").items():
                    add_ledger_entry(db, "evidence_ledger", finding["id"], kind, str(content), "legacy-migration", finding["created_at"])
            if not db.execute("SELECT 1 FROM counterevidence_ledger WHERE finding_id=?", (finding["id"],)).fetchone():
                for kind, content in json.loads(finding["counterevidence"] or "{}").items():
                    add_ledger_entry(db, "counterevidence_ledger", finding["id"], kind, str(content), "legacy-migration", finding["created_at"])


@asynccontextmanager
async def lifespan(_: FastAPI):
    prepare_database_upgrade(DB, DATA / "backups", ROOT)
    init_db()
    init_final_db()
    finalize_database_version(DB)
    scheduler_task = None
    if not os.getenv("PYTEST_CURRENT_TEST") and os.getenv("FIELDWORK_DISABLE_CAMPAIGN_SCHEDULER") != "1":
        async def scheduler_loop():
            mark_campaign_scheduler(running=True, last_error=None)
            while True:
                try:
                    processed = await asyncio.to_thread(run_due_campaign_schedules)
                    mark_campaign_scheduler(last_tick_at=now(), last_processed=len(processed), last_error=None)
                except Exception as error:
                    # Individual schedule failures are persisted by the scheduler.
                    # A database/startup race must not permanently kill the loop.
                    mark_campaign_scheduler(last_tick_at=now(), last_processed=0, last_error=type(error).__name__)
                await asyncio.sleep(30)
        scheduler_task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        if scheduler_task:
            scheduler_task.cancel()
            try:
                await scheduler_task
            except asyncio.CancelledError:
                pass
            mark_campaign_scheduler(running=False)
        shutdown_labs()


app = FastAPI(title="Security Research OS", version=APP_VERSION, lifespan=lifespan)
app.include_router(final_router)
app.include_router(web3_router)
app.include_router(web3_analysis_router)
app.include_router(web3_practice_router)
app.include_router(traditional_router)
app.include_router(traditional_tools_router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


class ManifestInput(BaseModel):
    manifest: str


class ScopeCheck(BaseModel):
    engagement_id: str
    url: str
    method: str = "GET"


class RunInput(BaseModel):
    adapter: str = "demo"


class SubmissionInput(BaseModel):
    platform: str = "manual"


class ReviewChecklist(BaseModel):
    scope_confirmed: bool = False
    no_unrelated_data: bool = False
    safe_payload: bool = False
    impact_accurate: bool = False
    evidence_redacted: bool = False


class RedirectChainInput(BaseModel):
    engagement_id: str
    urls: list[str]
    method: str = "GET"


class RuleParseInput(BaseModel):
    text: str
    program: str = "Imported SRC Program"


PROHIBITED_RULES = {
    "denial_of_service": ["dos", "ddos", "denial of service", "拒绝服务", "压力测试"],
    "credential_stuffing": ["credential stuffing", "撞库", "批量登录", "brute force", "暴力破解"],
    "social_engineering": ["social engineering", "社工", "钓鱼"],
    "real_payment": ["real payment", "真实支付", "真实资金", "实际付款"],
    "persistent_access": ["persistent access", "持久化", "后门"],
    "bulk_data_download": ["bulk download", "批量下载", "大量数据", "拖库"],
    "account_takeover": ["account takeover", "账号接管", "账户接管"],
}
DOMAIN_PATTERN = re.compile(r"(?i)(?:https?://)?(\*\.)?([a-z0-9](?:[a-z0-9-]{0,62}\.)+[a-z]{2,63})(?::\d+)?")


def parse_src_rules(text: str, program: str) -> dict[str, Any]:
    cleaned = text.strip()
    if len(cleaned) < 20:
        raise HTTPException(422, "SRC 规则文本过短，无法可靠解析")
    warnings: list[str] = []
    hosts: set[str] = set()
    wildcard_hosts: set[str] = set()
    for match in DOMAIN_PATTERN.finditer(cleaned):
        host = match.group(2).lower().rstrip(".")
        if match.group(1):
            wildcard_hosts.add(f"*.{host}")
        else:
            hosts.add(host)
    if wildcard_hosts:
        warnings.append(f"发现通配符范围但未自动展开：{', '.join(sorted(wildcard_hosts))}；请人工填写精确主机")
    if not hosts:
        warnings.append("未识别到精确授权主机；保存前必须人工补充 allowed_hosts")
    lowered = cleaned.lower()
    prohibited = sorted({rule for rule, keywords in PROHIBITED_RULES.items() if any(keyword in lowered for keyword in keywords)})
    if not prohibited:
        warnings.append("未识别到明确禁止项；已应用系统保守默认禁止清单")
        prohibited = ["denial_of_service", "credential_stuffing", "social_engineering", "real_payment", "persistent_access", "bulk_data_download"]
    expiry_match = re.search(r"(?:valid_until|截止日期|有效期至|授权至)\s*[:：]?\s*(20\d{2}-\d{2}-\d{2})", cleaned, re.I)
    valid_until = expiry_match.group(1) if expiry_match else (date.today() + timedelta(days=7)).isoformat()
    if not expiry_match:
        warnings.append("未识别到授权截止日期；临时设置为 7 天后，必须人工确认")
    manifest = {
        "engagement_id": f"src-import-{uuid.uuid4().hex[:8]}", "program": program.strip() or "Imported SRC Program",
        "valid_from": date.today().isoformat(), "valid_until": valid_until,
        "allowed_hosts": sorted(hosts), "denied_hosts": [], "allowed_methods": ["GET", "HEAD"],
        "allowed_ports": [443], "allowed_paths": ["/"], "denied_paths": [],
        "max_requests": 100, "max_tool_calls": 200, "max_requests_per_second": 1,
        "max_runtime_minutes": 30, "max_model_budget": 10,
        "allow_state_change": False, "allow_account_creation": False, "allow_oast": False,
        "allow_file_upload": False, "allow_private_ips": False, "prohibited": prohibited,
        "requires_confirmation": True, "source_text_sha256": content_hash(cleaned), "parser_warnings": warnings,
    }
    return {"manifest": manifest, "yaml": yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), "warnings": warnings, "wildcards": sorted(wildcard_hosts)}


SENSITIVE_KEY = re.compile(r'(?i)("?(?:authorization|cookie|token|secret|password|api[_-]?key)"?\s*[:=]\s*)[^,\n}\s]+')
BEARER = re.compile(r'(?i)bearer\s+[a-z0-9._~+/-]+=*')


def redact_text(value: str) -> str:
    value = BEARER.sub("Bearer [REDACTED]", value)
    return SENSITIVE_KEY.sub(r"\1[REDACTED]", value)


def normalize_target(target: str) -> str:
    parsed = urlparse(target)
    path = re.sub(r"/[0-9a-fA-F-]{8,}", "/{id}", parsed.path)
    return f"{parsed.scheme.lower()}://{(parsed.hostname or '').lower()}{path}"


def finding_fingerprint(category: str, target: str, title: str) -> str:
    normalized_title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", title.lower()).strip()
    material = f"{category}|{normalize_target(target)}|{normalized_title}"
    return hashlib.sha256(material.encode()).hexdigest()


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def add_ledger_entry(db: sqlite3.Connection, table: str, finding_id: str, kind: str, content: str, source: str, created_at: str | None = None) -> None:
    if table not in {"evidence_ledger", "counterevidence_ledger"}:
        raise ValueError("无效证据账本")
    entry_id = f"ev-{uuid.uuid4().hex[:10]}"
    db.execute(f"INSERT OR IGNORE INTO {table} VALUES(?,?,?,?,?,?,?)",
               (entry_id, finding_id, kind, content, content_hash(content), source, created_at or now()))


def verify_ledger(db: sqlite3.Connection, finding_id: str) -> dict[str, Any]:
    evidence = [dict(row) for row in db.execute("SELECT * FROM evidence_ledger WHERE finding_id=? ORDER BY kind", (finding_id,))]
    counter = [dict(row) for row in db.execute("SELECT * FROM counterevidence_ledger WHERE finding_id=? ORDER BY kind", (finding_id,))]
    tampered = [item["id"] for item in evidence + counter if content_hash(item["content"]) != item["sha256"]]
    evidence_kinds = {item["kind"] for item in evidence}
    counter_kinds = {item["kind"] for item in counter}
    required = {"baseline", "attack", "sha256"}
    return {
        "valid": not tampered and required.issubset(evidence_kinds) and "negative_control" in counter_kinds,
        "tampered_entries": tampered,
        "missing_evidence": sorted(required - evidence_kinds),
        "missing_counterevidence": [] if "negative_control" in counter_kinds else ["negative_control"],
        "evidence": evidence,
        "counterevidence": counter,
    }


def build_report(program: str, findings: list[sqlite3.Row]) -> str:
    verified = [item for item in findings if item["status"] == "verified"]
    if not verified:
        raise HTTPException(409, "没有已独立复验的漏洞，不能生成 SRC 提交草稿")
    lines = [f"# {program} — SRC 报告草稿", "", "> 状态：人工提交前草稿；系统不会自动提交。", ""]
    for item in verified:
        lines += [f"## {item['title']}", "", f"- 严重性：{item['severity']}", "- 验证状态：verified",
                  f"- 目标：{item['target']}", f"- 独立复验：{item['oracle']}（{item['replay_count']} 次）", "",
                  "### 证据", "", f"```json\n{redact_text(item['evidence'])}\n```", "",
                  "### 失败对照 / 反证", "", f"```json\n{redact_text(item['counterevidence'])}\n```", ""]
    return "\n".join(lines)


def findings_for_report(db: sqlite3.Connection, engagement_id: str) -> list[dict[str, Any]]:
    findings = [dict(row) for row in db.execute("SELECT * FROM findings WHERE engagement_id=?", (engagement_id,))]
    for finding in findings:
        ledger = verify_ledger(db, finding["id"])
        finding["evidence"] = json.dumps({item["kind"]: item["content"] for item in ledger["evidence"]}, ensure_ascii=False)
        finding["counterevidence"] = json.dumps({item["kind"]: item["content"] for item in ledger["counterevidence"]}, ensure_ascii=False)
    return findings


def parse_manifest(raw: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"Manifest YAML 无法解析：{exc}") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, "Manifest 必须是对象")
    required = ["engagement_id", "program", "allowed_hosts", "allowed_methods"]
    missing = [key for key in required if not value.get(key)]
    if missing:
        raise HTTPException(422, f"缺少字段：{', '.join(missing)}")
    value.setdefault("denied_hosts", [])
    value.setdefault("allowed_ports", [80, 443])
    value.setdefault("allowed_paths", ["/"])
    value.setdefault("denied_paths", [])
    value.setdefault("max_requests", 100)
    value.setdefault("max_tool_calls", 200)
    value.setdefault("max_model_budget", 10)
    value.setdefault("max_runtime_minutes", 30)
    value.setdefault("max_requests_per_second", 1)
    value.setdefault("allow_state_change", False)
    value.setdefault("allow_oast", False)
    value.setdefault("allow_private_ips", False)
    value.setdefault("prohibited", [])
    return value


def scope_decision(manifest: dict[str, Any], url: str, method: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        return False, "只允许有效的 HTTP/HTTPS 目标"
    if parsed.username or parsed.password:
        return False, "URL 不允许内嵌用户名或密码"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return False, "端口格式无效"
    today = date.today()
    try:
        if manifest.get("valid_from") and today < date.fromisoformat(str(manifest["valid_from"])):
            return False, "授权尚未生效"
        if manifest.get("valid_until") and today > date.fromisoformat(str(manifest["valid_until"])):
            return False, "授权已经过期"
    except ValueError:
        return False, "授权有效期格式无效"
    denied = {str(h).lower().rstrip(".") for h in manifest.get("denied_hosts", [])}
    allowed = {str(h).lower().rstrip(".") for h in manifest.get("allowed_hosts", [])}
    if host in denied:
        return False, "目标位于明确拒绝范围"
    if host not in allowed:
        return False, "目标不在授权主机清单"
    if port not in {int(p) for p in manifest.get("allowed_ports", [80, 443])}:
        return False, "目标端口未获授权"
    path = parsed.path or "/"
    denied_paths = [str(p) for p in manifest.get("denied_paths", [])]
    allowed_paths = [str(p) for p in manifest.get("allowed_paths", ["/"])]
    matches = lambda prefix: path == prefix or path.startswith(prefix.rstrip("/") + "/")
    if any(matches(prefix) for prefix in denied_paths):
        return False, "目标路径位于明确拒绝范围"
    if not any(matches(prefix) for prefix in allowed_paths):
        return False, "目标路径不在授权范围"
    if method.upper() not in {str(m).upper() for m in manifest.get("allowed_methods", [])}:
        return False, "HTTP 方法未获授权"
    if method.upper() not in {"GET", "HEAD", "OPTIONS"} and not manifest.get("allow_state_change"):
        return False, "当前项目禁止状态变更请求"
    return True, "范围、方法与安全模式均通过"


def ip_policy_decision(manifest: dict[str, Any], addresses: list[str]) -> tuple[bool, str]:
    if not addresses:
        return False, "DNS 未返回任何地址"
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return False, f"DNS 返回无效 IP：{raw}"
        restricted = address.is_private or address.is_loopback or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified
        if restricted and not manifest.get("allow_private_ips", False):
            return False, f"目标解析到私网或保留地址：{address}"
    return True, "所有解析地址均符合 IP 策略"


async def resolve_and_pin(engagement_id: str, manifest: dict[str, Any], host: str) -> tuple[bool, str, list[str]]:
    loop = asyncio.get_running_loop()
    try:
        records = await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except socket.gaierror as exc:
        return False, f"DNS 解析失败：{exc}", []
    addresses = sorted({record[4][0] for record in records})
    allowed, reason = ip_policy_decision(manifest, addresses)
    if not allowed:
        return False, reason, addresses
    with connect() as db:
        pinned = db.execute("SELECT addresses FROM dns_pins WHERE engagement_id=? AND host=?", (engagement_id, host)).fetchone()
        if pinned:
            prior = sorted(json.loads(pinned["addresses"]))
            if prior != addresses:
                return False, f"DNS 地址集合发生变化，疑似 Rebinding：{prior} → {addresses}", addresses
            db.execute("UPDATE dns_pins SET last_checked_at=? WHERE engagement_id=? AND host=?", (now(), engagement_id, host))
        else:
            db.execute("INSERT INTO dns_pins VALUES(?,?,?,?,?)", (engagement_id, host, json.dumps(addresses), now(), now()))
    return True, "DNS/IP 校验通过且地址已钉扎", addresses


def emit(run_id: str, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
    with connect() as db:
        db.execute("INSERT INTO events(run_id,kind,message,payload,created_at) VALUES(?,?,?,?,?)",
                   (run_id, kind, message, json.dumps(payload or {}, ensure_ascii=False), now()))


def audit(engagement_id: str, action: str, detail: str) -> None:
    with connect() as db:
        db.execute("INSERT INTO audit_log(engagement_id,action,detail,created_at) VALUES(?,?,?,?)",
                   (engagement_id, action, detail, now()))


def consume_budget(run_id: str, category: str, amount: int = 1) -> tuple[bool, str]:
    fields = {
        "request": ("requests_used", "request_limit", "请求"),
        "tool_call": ("tool_calls_used", "tool_call_limit", "工具调用"),
        "model_cost_micros": ("model_cost_micros", "model_budget_micros", "模型费用"),
    }
    if category not in fields or amount < 0:
        raise ValueError("无效预算消费")
    used_field, limit_field, label = fields[category]
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        budget = db.execute("SELECT * FROM budgets WHERE run_id=?", (run_id,)).fetchone()
        if not budget:
            db.rollback()
            return False, "预算账本不存在"
        if budget[used_field] + amount > budget[limit_field]:
            db.rollback()
            return False, f"{label}预算不足：{budget[used_field]} + {amount} > {budget[limit_field]}"
        db.execute(f"UPDATE budgets SET {used_field}={used_field}+?,updated_at=? WHERE run_id=?", (amount, now(), run_id))
        if category == "request":
            db.execute("UPDATE runs SET requests_used=requests_used+? WHERE id=?", (amount, run_id))
    return True, f"{label}预算已消费 {amount}"


async def demo_run(run_id: str, engagement_id: str, manifest: dict[str, Any]) -> None:
    steps = [
        ("gate", "授权闸门已加载，所有工具调用将先执行范围检查"),
        ("recon", "正在建立低风险攻击面清单"),
        ("coverage", "发现登录、会话与对象访问控制测试面"),
        ("agent", "已按攻击面启用 Auth 与 Access Control 检查器"),
    ]
    with connect() as db:
        db.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (now(), run_id))
    for kind, message in steps:
        await asyncio.sleep(0.7)
        with connect() as db:
            state = db.execute("SELECT status,requests_used,request_limit,started_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if not state or state["status"] == "stopped":
                emit(run_id, "stop", "任务已安全停止，未继续调用工具")
                return
            started = datetime.fromisoformat(state["started_at"])
            elapsed_minutes = (datetime.now(timezone.utc) - started).total_seconds() / 60
            if elapsed_minutes >= float(manifest.get("max_runtime_minutes", 30)):
                db.execute("UPDATE runs SET status='budget_exhausted',stopped_at=? WHERE id=?", (now(), run_id))
                db.commit()
                emit(run_id, "budget", "运行时间预算已耗尽，任务立即失败关闭")
                audit(engagement_id, "run.budget_exhausted", f"{run_id} 达到运行时间上限")
                return
        budget_ok, budget_reason = consume_budget(run_id, "request", 1)
        if not budget_ok:
            with connect() as db:
                db.execute("UPDATE runs SET status='budget_exhausted',stopped_at=? WHERE id=?", (now(), run_id))
            emit(run_id, "budget", f"{budget_reason}；任务立即失败关闭")
            audit(engagement_id, "run.budget_exhausted", f"{run_id} · {budget_reason}")
            return
        tool_ok, tool_reason = consume_budget(run_id, "tool_call", 1)
        if not tool_ok:
            with connect() as db:
                db.execute("UPDATE runs SET status='budget_exhausted',stopped_at=? WHERE id=?", (now(), run_id))
            emit(run_id, "budget", f"{tool_reason}；任务立即失败关闭")
            audit(engagement_id, "run.budget_exhausted", f"{run_id} · {tool_reason}")
            return
        emit(run_id, kind, message)
    target = f"https://{manifest['allowed_hosts'][0]}/api/profile"
    surface_id = f"surface-{uuid.uuid4().hex[:8]}"
    hypothesis_id = f"hypothesis-{uuid.uuid4().hex[:8]}"
    fid = f"finding-{uuid.uuid4().hex[:8]}"
    baseline = "GET /api/profile → 200，返回当前测试账号对象"
    attack = "GET /api/profile?id=other-test-object → 200，响应结构出现差异"
    digest = hashlib.sha256(f"{baseline}\n{attack}".encode()).hexdigest()
    fingerprint = finding_fingerprint("access_control", target, "对象访问控制差异（待独立复验）")
    prior_occurrences = 0
    with connect() as db:
        db.execute("""INSERT INTO surfaces VALUES(?,?,?,?,?,?,?,?,?)
          ON CONFLICT(engagement_id,url,method,role) DO UPDATE SET state='tested'""", (
            surface_id, engagement_id, target, "GET", "authenticated_user", "profile", "demo-recon", "tested", now()))
        actual_surface = db.execute("SELECT id FROM surfaces WHERE engagement_id=? AND url=? AND method='GET' AND role='authenticated_user'", (engagement_id, target)).fetchone()["id"]
        db.execute("INSERT INTO hypotheses VALUES(?,?,?,?,?,?,?,?,?)", (
            hypothesis_id, engagement_id, actual_surface, "access_control",
            "更换对象标识后，服务端可能未验证对象所有权", "candidate", fid, now(), now()))
        db.execute("""INSERT INTO findings VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (
            fid, engagement_id, "对象访问控制差异（待独立复验）", "medium", "candidate", target,
            json.dumps({"baseline": baseline, "attack": attack, "sha256": digest}, ensure_ascii=False),
            json.dumps({"negative_control": "无效对象 ID 返回 404；尚未证明跨用户数据可读"}, ensure_ascii=False),
            0, None, now()))
        add_ledger_entry(db, "evidence_ledger", fid, "baseline", baseline, "demo-runner")
        add_ledger_entry(db, "evidence_ledger", fid, "attack", attack, "demo-runner")
        add_ledger_entry(db, "evidence_ledger", fid, "sha256", digest, "demo-runner")
        add_ledger_entry(db, "counterevidence_ledger", fid, "negative_control", "无效对象 ID 返回 404；尚未证明跨用户数据可读", "demo-runner")
        memory = db.execute("SELECT occurrences FROM vulnerability_memory WHERE fingerprint=?", (fingerprint,)).fetchone()
        if memory:
            prior_occurrences = memory["occurrences"]
            db.execute("UPDATE vulnerability_memory SET latest_finding_id=?,occurrences=occurrences+1,last_seen_at=? WHERE fingerprint=?", (fid, now(), fingerprint))
        else:
            db.execute("INSERT INTO vulnerability_memory VALUES(?,?,?,?,?,?,?,?)", (
                fingerprint, "access_control", normalize_target(target), "对象访问控制差异", fid, 1, now(), now()))
        db.execute("UPDATE runs SET status='completed', stopped_at=? WHERE id=?", (now(), run_id))
        coverage = [
            (engagement_id, "身份认证与会话", "tested", "已执行登录态与会话差异检查", now()),
            (engagement_id, "对象访问控制", "open_proof_gap", "发现响应差异，等待独立复验", now()),
            (engagement_id, "输入与注入", "not_tested", "本轮未发现需要启动该检查器的输入面", now()),
            (engagement_id, "业务逻辑", "not_tested", "需要测试账号与业务状态机后再测试", now()),
        ]
        db.executemany("""INSERT INTO coverage(engagement_id,surface,state,note,updated_at) VALUES(?,?,?,?,?)
          ON CONFLICT(engagement_id,surface) DO UPDATE SET state=excluded.state,note=excluded.note,updated_at=excluded.updated_at""", coverage)
    emit(run_id, "candidate", "发现 1 条候选：仅记录为 candidate，等待独立复验", {"finding_id": fid})
    if prior_occurrences:
        emit(run_id, "memory", f"历史指纹命中：此前出现 {prior_occurrences} 次；保留为新观察记录但不计为新漏洞", {"fingerprint": fingerprint})
    emit(run_id, "complete", "本轮安全结束；预算未超限，证据已写入 SQLite")


async def adapter_run(run_id: str, engagement_id: str, manifest: dict[str, Any], adapter: str) -> None:
    target_path = str(manifest.get("allowed_paths", ["/"])[0])
    target = f"https://{manifest['allowed_hosts'][0]}{target_path}"
    allowed, reason = scope_decision(manifest, target, "GET")
    if not allowed:
        with connect() as db:
            db.execute("UPDATE runs SET status='failed',stopped_at=? WHERE id=?", (now(), run_id))
        emit(run_id, "gate", f"Adapter 启动前范围检查失败：{reason}")
        return
    host = urlparse(target).hostname or ""
    dns_allowed, dns_reason, addresses = await resolve_and_pin(engagement_id, manifest, host)
    if not dns_allowed:
        with connect() as db:
            db.execute("UPDATE runs SET status='failed',stopped_at=? WHERE id=?", (now(), run_id))
        emit(run_id, "dns", f"Adapter 启动前 DNS/IP 检查失败：{dns_reason}", {"addresses": addresses})
        audit(engagement_id, "dns.denied", f"{host} · {dns_reason}")
        return
    emit(run_id, "dns", dns_reason, {"host": host, "addresses": addresses})
    try:
        args = build_command(adapter, engagement_id, target)
    except (ValueError, RuntimeError, KeyError) as exc:
        with connect() as db:
            db.execute("UPDATE runs SET status='failed',stopped_at=? WHERE id=?", (now(), run_id))
        emit(run_id, "adapter", f"Adapter 配置错误：{exc}")
        return
    execution_id = f"exec-{uuid.uuid4().hex[:8]}"
    with connect() as db:
        db.execute("UPDATE runs SET status='running',started_at=? WHERE id=?", (now(), run_id))
        db.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?)",
                   (execution_id, run_id, adapter, args[0], "running", None, now(), None))
    emit(run_id, "adapter", f"{adapter} 已通过 argv-only 子进程启动；未使用 shell")
    exit_code: int | None = None
    terminal_status = "failed"
    async for channel, line in stream_process(args, float(manifest.get("max_runtime_minutes", 30)) * 60, run_id):
        if channel == "exit":
            exit_code = int(line)
            terminal_status = "completed" if exit_code == 0 else "failed"
        elif channel == "timeout":
            terminal_status = "budget_exhausted"
            emit(run_id, "budget", line)
        else:
            tool_ok, tool_reason = consume_budget(run_id, "tool_call", 1)
            if not tool_ok:
                terminal_status = "budget_exhausted"
                emit(run_id, "budget", tool_reason)
                await stop_process(run_id)
                break
            if line.startswith("SRC_CONTROL_EVENT "):
                try:
                    event = json.loads(line.removeprefix("SRC_CONTROL_EVENT "))
                    event_kind = event.get("kind")
                    amount = int(event.get("amount", 1))
                    budget_ok, budget_reason = consume_budget(run_id, event_kind, amount)
                    if not budget_ok:
                        terminal_status = "budget_exhausted"
                        emit(run_id, "budget", budget_reason)
                        await stop_process(run_id)
                        break
                    emit(run_id, "budget", budget_reason)
                    continue
                except (ValueError, TypeError, json.JSONDecodeError):
                    emit(run_id, "adapter", "忽略了无效的 SRC_CONTROL_EVENT")
                    continue
            emit(run_id, "tool", f"[{channel}] {line[:2000]}")
    with connect() as db:
        current = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
        if current and current["status"] == "stopped":
            terminal_status = "stopped"
        db.execute("UPDATE executions SET status=?,exit_code=?,stopped_at=? WHERE id=?",
                   (terminal_status, exit_code, now(), execution_id))
        db.execute("UPDATE runs SET status=?,stopped_at=? WHERE id=?", (terminal_status, now(), run_id))
    audit(engagement_id, "adapter.completed", f"{adapter}/{execution_id} → {terminal_status}")
    emit(run_id, "complete" if terminal_status == "completed" else "adapter", f"{adapter} 运行结束：{terminal_status}")


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/new", response_class=HTMLResponse)
@app.get("/run", response_class=HTMLResponse)
@app.get("/runs/{resource_id}", response_class=HTMLResponse)
@app.get("/findings", response_class=HTMLResponse)
@app.get("/findings/{resource_id}", response_class=HTMLResponse)
@app.get("/reports", response_class=HTMLResponse)
@app.get("/reports/{resource_id}", response_class=HTMLResponse)
@app.get("/settings", response_class=HTMLResponse)
def final_workspace(request: Request, resource_id: str | None = None):
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/overview")
def overview():
    with connect() as db:
        engagements = [dict(r) for r in db.execute("SELECT id,program,status,confirmed_at,created_at FROM engagements ORDER BY created_at DESC")]
        findings = [dict(r) for r in db.execute("SELECT * FROM findings ORDER BY created_at DESC")]
        runs = [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at DESC")]
        coverage = [dict(r) for r in db.execute("SELECT * FROM coverage ORDER BY updated_at DESC")]
        submissions = [dict(r) for r in db.execute("SELECT id,engagement_id,platform,status,checklist,created_at,updated_at FROM submissions ORDER BY updated_at DESC")]
        budgets = [dict(r) for r in db.execute("SELECT b.*,r.engagement_id,r.status,r.adapter FROM budgets b JOIN runs r ON r.id=b.run_id ORDER BY b.updated_at DESC")]
        surfaces = [dict(r) for r in db.execute("SELECT * FROM surfaces ORDER BY discovered_at DESC")]
        hypotheses = [dict(r) for r in db.execute("SELECT * FROM hypotheses ORDER BY updated_at DESC")]
        memory = [dict(r) for r in db.execute("SELECT * FROM vulnerability_memory ORDER BY last_seen_at DESC")]
    return {"engagements": engagements, "findings": findings, "runs": runs, "coverage": coverage, "submissions": submissions, "budgets": budgets, "surfaces": surfaces, "hypotheses": hypotheses, "memory": memory}


@app.get("/api/runtime")
def runtime_status():
    return {"adapters": [status.__dict__ for status in adapter_statuses()]}


@app.get("/api/engagements/{eid}")
def engagement_detail(eid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        runs = [dict(r) for r in db.execute("SELECT * FROM runs WHERE engagement_id=? ORDER BY started_at DESC", (eid,))]
        audit_rows = [dict(r) for r in db.execute("SELECT * FROM audit_log WHERE engagement_id=? ORDER BY id DESC LIMIT 30", (eid,))]
        dns_pins = [dict(r) for r in db.execute("SELECT * FROM dns_pins WHERE engagement_id=? ORDER BY host", (eid,))]
    if not row:
        raise HTTPException(404, "项目不存在")
    return {**dict(row), "manifest_data": yaml.safe_load(row["manifest"]), "runs": runs, "audit": audit_rows, "dns_pins": dns_pins}


@app.get("/api/findings/{fid}")
def finding_detail(fid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
        ledger = verify_ledger(db, fid) if row else None
    if not row:
        raise HTTPException(404, "候选不存在")
    value = dict(row)
    value["evidence_data"] = json.loads(value["evidence"])
    value["counterevidence_data"] = json.loads(value["counterevidence"])
    value["ledger"] = ledger
    return value


@app.get("/api/findings/{fid}/integrity")
def finding_integrity(fid: str):
    with connect() as db:
        if not db.execute("SELECT 1 FROM findings WHERE id=?", (fid,)).fetchone():
            raise HTTPException(404, "候选不存在")
        return verify_ledger(db, fid)


@app.post("/api/engagements")
def create_engagement(body: ManifestInput):
    manifest = parse_manifest(body.manifest)
    eid = str(manifest["engagement_id"])
    with connect() as db:
        if db.execute("SELECT 1 FROM engagements WHERE id=?", (eid,)).fetchone():
            raise HTTPException(409, "该 engagement_id 已存在")
        db.execute("INSERT INTO engagements VALUES(?,?,?,?,?,?)",
                   (eid, manifest["program"], "draft", yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), None, now()))
    audit(eid, "engagement.created", "Manifest 已保存为草稿，尚未授权执行")
    return {"id": eid, "status": "draft", "manifest": manifest}


@app.post("/api/manifests/parse-rules")
def parse_rules(body: RuleParseInput):
    return parse_src_rules(body.text, body.program)


@app.post("/api/engagements/{eid}/confirm")
def confirm(eid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        manifest = yaml.safe_load(row["manifest"])
        manifest["requires_confirmation"] = False
        db.execute("UPDATE engagements SET status='confirmed',manifest=?,confirmed_at=? WHERE id=?",
                   (yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), now(), eid))
    audit(eid, "engagement.confirmed", "用户人工确认了目标范围与禁止项")
    return {"id": eid, "status": "confirmed"}


@app.post("/api/scope/check")
def check_scope(body: ScopeCheck):
    with connect() as db:
        row = db.execute("SELECT manifest FROM engagements WHERE id=?", (body.engagement_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    allowed, reason = scope_decision(yaml.safe_load(row["manifest"]), body.url, body.method)
    audit(body.engagement_id, "scope.allowed" if allowed else "scope.denied", f"{body.method.upper()} {body.url} · {reason}")
    return {"allowed": allowed, "reason": reason, "url": body.url, "method": body.method.upper()}


@app.post("/api/scope/redirect-chain")
def check_redirect_chain(body: RedirectChainInput):
    if not body.urls or len(body.urls) > 20:
        raise HTTPException(422, "重定向链必须包含 1–20 个 URL")
    with connect() as db:
        row = db.execute("SELECT manifest FROM engagements WHERE id=?", (body.engagement_id,)).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    manifest = yaml.safe_load(row["manifest"])
    hops = []
    for index, url in enumerate(body.urls):
        allowed, reason = scope_decision(manifest, url, body.method)
        hops.append({"index": index, "url": url, "allowed": allowed, "reason": reason})
        if not allowed:
            audit(body.engagement_id, "redirect.denied", f"hop={index} {url} · {reason}")
            return {"allowed": False, "blocked_at": index, "hops": hops}
    audit(body.engagement_id, "redirect.allowed", f"{len(hops)} hops 均在范围内")
    return {"allowed": True, "blocked_at": None, "hops": hops}


@app.post("/api/engagements/{eid}/runs")
async def start_run(eid: str, body: RunInput = RunInput()):
    with connect() as db:
        row = db.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "项目不存在")
        if row["status"] != "confirmed":
            raise HTTPException(409, "必须先人工确认授权范围")
        active = db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone()
        if active:
            raise HTTPException(409, "个人版同时只允许运行一个任务")
        manifest = yaml.safe_load(row["manifest"])
        statuses = {item.name: item for item in adapter_statuses() if item.kind == "runner"}
        if body.adapter not in statuses:
            raise HTTPException(422, "未知运行器")
        if not statuses[body.adapter].available:
            raise HTTPException(409, statuses[body.adapter].detail)
        rid = f"run-{uuid.uuid4().hex[:8]}"
        db.execute("INSERT INTO runs(id,engagement_id,status,request_limit,adapter) VALUES(?,?,?,?,?)",
                   (rid, eid, "queued", int(manifest.get("max_requests", 100)), body.adapter))
        db.execute("INSERT INTO budgets VALUES(?,?,?,?,?,?,?,?)", (
            rid, int(manifest.get("max_requests", 100)), 0,
            int(manifest.get("max_tool_calls", 200)), 0,
            int(float(manifest.get("max_model_budget", 10)) * 1_000_000), 0, now()))
    emit(rid, "queue", "任务已进入单任务队列")
    audit(eid, "run.started", f"{rid} 已进入单任务队列，adapter={body.adapter}")
    if body.adapter == "demo":
        asyncio.create_task(demo_run(rid, eid, manifest))
    else:
        asyncio.create_task(adapter_run(rid, eid, manifest, body.adapter))
    return {"id": rid, "status": "queued"}


@app.post("/api/runs/{rid}/stop")
async def stop_run(rid: str):
    with connect() as db:
        row = db.execute("SELECT engagement_id FROM runs WHERE id=?", (rid,)).fetchone()
        cur = db.execute("UPDATE runs SET status='stopped', stopped_at=? WHERE id=? AND status IN ('queued','running')", (now(), rid))
        if not cur.rowcount:
            raise HTTPException(409, "任务不存在或已结束")
    audit(row["engagement_id"], "run.stopped", f"{rid} 已由用户安全停止")
    await stop_process(rid)
    return {"id": rid, "status": "stopped"}


@app.get("/api/runs/{rid}/events")
async def stream_events(rid: str):
    async def generate():
        cursor = 0
        quiet = 0
        while quiet < 30:
            with connect() as db:
                rows = db.execute("SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (rid, cursor)).fetchall()
                state = db.execute("SELECT status FROM runs WHERE id=?", (rid,)).fetchone()
            if rows:
                quiet = 0
                for row in rows:
                    cursor = row["id"]
                    yield f"data: {json.dumps(dict(row), ensure_ascii=False)}\n\n"
            else:
                quiet += 1
                yield ": ping\n\n"
            if state and state["status"] in {"completed", "stopped", "failed", "interrupted", "budget_exhausted"} and not rows:
                break
            await asyncio.sleep(0.5)
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/findings/{fid}/verify")
def verify_finding(fid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM findings WHERE id=?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "候选不存在")
        integrity = verify_ledger(db, fid)
        status = "verified" if integrity["valid"] else "open_proof_gap"
        db.execute("UPDATE findings SET status=?, replay_count=2, oracle='deterministic-demo-oracle' WHERE id=?", (status, fid))
        if status == "verified":
            db.execute("UPDATE coverage SET state='tested',note='候选已完成失败对照与 2 次独立重放',updated_at=? WHERE engagement_id=? AND surface='对象访问控制'", (now(), row["engagement_id"]))
        db.execute("UPDATE hypotheses SET status=?,updated_at=? WHERE finding_id=?", (status, now(), fid))
    audit(row["engagement_id"], "finding.verified" if status == "verified" else "finding.proof_gap", f"{fid} → {status}")
    return {"id": fid, "status": status, "replay_count": 2, "oracle": "deterministic-demo-oracle"}


@app.get("/api/engagements/{eid}/report")
def report(eid: str):
    with connect() as db:
        eng = db.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        findings = findings_for_report(db, eid)
    if not eng:
        raise HTTPException(404, "项目不存在")
    content = build_report(eng["program"], findings)
    return JSONResponse({"filename": f"{eid}-report.md", "content": content})


@app.post("/api/engagements/{eid}/submissions")
def create_submission(eid: str, body: SubmissionInput):
    with connect() as db:
        eng = db.execute("SELECT * FROM engagements WHERE id=?", (eid,)).fetchone()
        findings = findings_for_report(db, eid)
        if not eng:
            raise HTTPException(404, "项目不存在")
        content = build_report(eng["program"], findings)
        sid = f"submission-{uuid.uuid4().hex[:8]}"
        checklist = ReviewChecklist().model_dump()
        db.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?)",
                   (sid, eid, body.platform, "draft", content, json.dumps(checklist), now(), now()))
    audit(eid, "submission.created", f"{sid} 已生成；platform={body.platform}；不会自动发送")
    return {"id": sid, "status": "draft"}


@app.get("/api/submissions/{sid}")
def submission_detail(sid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "提交草稿不存在")
    value = dict(row)
    value["checklist_data"] = json.loads(value["checklist"])
    return value


@app.put("/api/submissions/{sid}/review")
def review_submission(sid: str, body: ReviewChecklist):
    checklist = body.model_dump()
    status = "review_ready" if all(checklist.values()) else "draft"
    with connect() as db:
        row = db.execute("SELECT engagement_id FROM submissions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise HTTPException(404, "提交草稿不存在")
        db.execute("UPDATE submissions SET status=?,checklist=?,updated_at=? WHERE id=?",
                   (status, json.dumps(checklist), now(), sid))
    audit(row["engagement_id"], "submission.reviewed", f"{sid} → {status}")
    return {"id": sid, "status": status, "checklist": checklist}


@app.get("/api/submissions/{sid}/export")
def export_submission(sid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "提交草稿不存在")
    if row["status"] != "review_ready":
        raise HTTPException(409, "提交前检查清单尚未全部确认")
    return {"filename": f"{sid}.md", "content": row["report"], "status": row["status"]}
