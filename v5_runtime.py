"""V5 local-first runtime profiles, provider routing, and usage ledger."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

import runtime_secrets


router = APIRouter(prefix="/api/v1/runtime", tags=["V5 Runtime"])
LOCAL_KINDS = {"ollama", "llama_cpp", "local"}
SENSITIVE_NAMES = {"authorization", "cookie", "password", "secret", "token", "api_key", "private_key"}


def _core():
    import final_core
    return final_core


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _load(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _provider_config_hash(row: sqlite3.Row) -> str:
    return _sha({key: row[key] for key in ("id", "kind", "base_url", "model", "metadata_json")})


def _emit(db: sqlite3.Connection, entity_id: str, event_type: str,
          payload: dict[str, Any], campaign_id: str | None = None) -> None:
    db.execute(
        "INSERT INTO v5_events(topic,campaign_id,entity_id,event_type,payload_json,created_at) "
        "VALUES(?,?,?,?,?,?)",
        ("runtime", campaign_id, entity_id, event_type, _dump(payload), _now()),
    )


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (normalized in SENSITIVE_NAMES or normalized.endswith(("_token", "_secret", "_password", "_cookie"))
                    or _contains_sensitive_key(item)):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _location(row: sqlite3.Row) -> str:
    metadata = _load(row["metadata_json"], {})
    return metadata.get("location", "local" if row["kind"] in LOCAL_KINDS else "cloud")


def _provider_view(row: sqlite3.Row) -> dict[str, Any]:
    metadata = _load(row["metadata_json"], {})
    safe_metadata = {key: metadata[key] for key in (
        "priority", "tier", "supports_independence", "cost_micros_per_million_tokens", "max_context_tokens", "input_token_counting"
    ) if key in metadata}
    return {
        "id": row["id"], "kind": row["kind"], "name": row["name"],
        "location": _location(row), "base_url": row["base_url"], "model": row["model"],
        "enabled": bool(row["enabled"]), "metadata": safe_metadata,
        "configuration_sha256": _provider_config_hash(row),
        "secret_configured": bool(row["secret_ref"]), "last_health": row["last_health"] or "unknown",
        "last_health_at": row["last_health_at"], "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _profile_view(row: sqlite3.Row) -> dict[str, Any]:
    raw = _load(row["config_json"], {})
    try:
        config = ProfileConfig.model_validate(raw).model_dump(mode="json")
    except Exception:
        config = {key: raw[key] for key in (
            "local_provider_ids", "cloud_provider_ids", "independent_provider_ids", "allow_fallback",
            "cloud_complexity_threshold", "max_tokens_per_call", "max_cost_micros",
        ) if key in raw}
    return {
        "id": row["id"], "name": row["name"], "mode": row["mode"],
        "config": config, "immutable_hash": row["immutable_hash"],
        "provider_configuration_bound": "provider_config_hashes" in raw,
        "created_at": row["created_at"],
    }


def _validated_base_url(value: str, location: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "provider URL must be an absolute HTTP(S) URL without credentials")
    if parsed.query or parsed.fragment:
        raise HTTPException(422, "provider URL must not contain a query or fragment")
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as error:
        raise HTTPException(422, "provider URL has an invalid port") from error
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    loopback = host == "localhost" or bool(literal and literal.is_loopback)
    if location == "local" and (not loopback or port is None):
        raise HTTPException(422, "local providers must use an explicit loopback endpoint")
    if location == "cloud":
        if parsed.scheme != "https" or loopback or (literal and not literal.is_global):
            raise HTTPException(422, "cloud providers require a public HTTPS endpoint")
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port:
        netloc += f":{port}"
    return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


class ProviderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    priority: int = Field(default=100, ge=0, le=10000)
    tier: Literal["small", "large", "frontier", "verifier"] = "small"
    supports_independence: bool = False
    cost_micros_per_million_tokens: int = Field(default=0, ge=0, le=10_000_000_000)
    max_context_tokens: int = Field(default=32768, ge=1, le=10_000_000)
    input_token_counting: Literal['utf8_estimate', 'llama_cpp_server'] = 'utf8_estimate'


class ProviderCreate(BaseModel):
    kind: str = Field(default="openai_compatible", min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    location: Literal["local", "cloud"]
    base_url: str = Field(min_length=8, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(default=None, min_length=8, max_length=16384)
    enabled: bool = True
    metadata: ProviderMetadata = Field(default_factory=ProviderMetadata)


class ProviderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: bool | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=16384)
    clear_secret: bool = False


class ProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_provider_ids: list[str] = Field(default_factory=list, max_length=100)
    cloud_provider_ids: list[str] = Field(default_factory=list, max_length=100)
    independent_provider_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_fallback: bool = True
    cloud_complexity_threshold: float = Field(default=.65, ge=0, le=1)
    max_tokens_per_call: int = Field(default=32768, ge=1, le=2_000_000)
    max_cost_micros: int = Field(default=0, ge=0)
    max_concurrent_calls: int = Field(default=1, strict=True, ge=1, le=32)
    max_tokens_per_hour: int = Field(default=1_000_000, strict=True, ge=1, le=100_000_000)
    max_runtime_ms_per_call: int = Field(default=45_000, strict=True, ge=1, le=45_000)
    provider_config_hashes: dict[str, str] = Field(default_factory=dict, max_length=300)


class ProfileCreate(BaseModel):
    name: str = Field(default="Default", min_length=1, max_length=120)
    mode: Literal["cloud", "local", "hybrid", "offline"] = "hybrid"
    config: ProfileConfig = Field(default_factory=ProfileConfig)


class RouteRequest(BaseModel):
    task_type: str = Field(min_length=1, max_length=120)
    sensitivity: Literal["public", "internal", "private", "secret"] = "internal"
    complexity: float = Field(default=.5, ge=0, le=1)
    estimated_tokens: int = Field(default=4000, ge=1, le=2_000_000)
    requires_independence: bool = False
    budget_remaining_micros: int = Field(default=0, ge=0)
    profile_id: str | None = Field(default=None, max_length=200)
    mode: Literal["cloud", "local", "hybrid", "offline"] | None = None
    campaign_id: str | None = Field(default=None, max_length=200)
    task_id: str | None = Field(default=None, max_length=200)


class UsageReport(BaseModel):
    decision_id: str = Field(min_length=1, max_length=200)
    call_id: str | None = Field(default=None, min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=200)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_micros: int = Field(default=0, ge=0)
    runtime_ms: int | None = Field(default=None, strict=True, ge=0)


@router.post("/providers", status_code=201)
def create_provider(body: ProviderCreate):
    if body.metadata.input_token_counting == 'llama_cpp_server' and (body.location != 'local' or body.kind != 'llama_cpp'):
        raise HTTPException(422, 'server input counting requires local llama.cpp')
    base_url = _validated_base_url(body.base_url, body.location)
    metadata = body.metadata.model_dump(mode="json")
    metadata["location"] = body.location
    provider_id, secret_ref, now = _uid("provider"), None, _now()
    if body.api_key:
        secret_ref = f"runtime-{provider_id}"
        try:
            runtime_secrets.put(secret_ref, body.api_key)
        except Exception as error:
            raise HTTPException(503, f"runtime secret store unavailable: {type(error).__name__}") from error
    f = _core()
    try:
        with f.connect() as db:
            db.execute(
                "INSERT INTO runtime_providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (provider_id, body.kind, body.name, base_url, body.model, secret_ref,
                 1 if body.enabled else 0, _dump(metadata), "unknown", None, now, now),
            )
            _emit(db, provider_id, "provider.created", {"kind": body.kind, "location": body.location})
    except Exception:
        if secret_ref:
            runtime_secrets.delete(secret_ref)
        raise
    return get_provider(provider_id)


@router.get("/providers/{provider_id}")
def get_provider(provider_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM runtime_providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, "runtime provider not found")
    return _provider_view(row)


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: str, body: ProviderUpdate):
    if body.api_key and body.clear_secret:
        raise HTTPException(422, "api_key and clear_secret are mutually exclusive")
    f, now = _core(), _now()
    with f.connect() as db:
        row = db.execute("SELECT * FROM runtime_providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, "runtime provider not found")
    secret_ref = row["secret_ref"]
    previous_secret = None
    created_reference = False
    if body.api_key:
        if secret_ref:
            try:
                previous_secret = runtime_secrets.get(secret_ref)
            except Exception as error:
                raise HTTPException(503, f"runtime secret store unavailable: {type(error).__name__}") from error
        else:
            created_reference = True
        secret_ref = secret_ref or f"runtime-{provider_id}"
        try:
            runtime_secrets.put(secret_ref, body.api_key)
        except Exception as error:
            raise HTTPException(503, f"runtime secret store unavailable: {type(error).__name__}") from error
    assignments, values = ["updated_at=?"], [now]
    for column, value in (("name", body.name), ("model", body.model)):
        if value is not None:
            assignments.append(f"{column}=?")
            values.append(value)
    if body.enabled is not None:
        assignments.append("enabled=?")
        values.append(1 if body.enabled else 0)
    if body.api_key or body.clear_secret:
        assignments.append("secret_ref=?")
        values.append(None if body.clear_secret else secret_ref)
    if (body.model is not None and body.model != row["model"]) or body.api_key or body.clear_secret:
        assignments.extend(["last_health='unknown'", "last_health_at=NULL"])
    values.append(provider_id)
    try:
        with f.connect() as db:
            db.execute(f"UPDATE runtime_providers SET {','.join(assignments)} WHERE id=?", values)
            _emit(db, provider_id, "provider.updated", {"fields": sorted(body.model_fields_set - {"api_key"}),
                                                         "secret_changed": bool(body.api_key or body.clear_secret)})
    except Exception:
        if body.api_key:
            if created_reference:
                runtime_secrets.delete(secret_ref)
            elif previous_secret is not None:
                runtime_secrets.put(secret_ref, previous_secret)
        raise
    if body.clear_secret and row["secret_ref"]:
        runtime_secrets.delete(row["secret_ref"])
    return get_provider(provider_id)


@router.get("/providers")
def list_providers():
    f = _core()
    with f.connect() as db:
        rows = db.execute("SELECT * FROM runtime_providers ORDER BY created_at,id").fetchall()
    return {"items": [_provider_view(row) for row in rows]}


def _validate_profile_providers(db: sqlite3.Connection, config: ProfileConfig) -> None:
    groups = ((config.local_provider_ids, "local"), (config.cloud_provider_ids, "cloud"))
    all_ids = [provider_id for ids, _ in groups for provider_id in ids] + config.independent_provider_ids
    if any(len(values) != len(set(values)) for values in (
        config.local_provider_ids, config.cloud_provider_ids, config.independent_provider_ids,
    )):
        raise HTTPException(422, "runtime profile provider ids must be unique within each route")
    rows = {row["id"]: row for row in db.execute(
        f"SELECT * FROM runtime_providers WHERE id IN ({','.join('?' for _ in all_ids)})", all_ids,
    ).fetchall()} if all_ids else {}
    if set(all_ids) != set(rows):
        raise HTTPException(404, "runtime profile references an unknown provider")
    for ids, expected in groups:
        if any(_location(rows[item]) != expected for item in ids):
            raise HTTPException(409, f"runtime profile {expected} provider classification mismatch")
    for provider_id in config.independent_provider_ids:
        if not _load(rows[provider_id]["metadata_json"], {}).get("supports_independence"):
            raise HTTPException(409, "independent route references a provider without independence capability")


@router.put("/config", status_code=201)
def create_profile(body: ProfileCreate):
    config = body.config.model_dump(mode="json")
    now = _now()
    f = _core()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        _validate_profile_providers(db, body.config)
        provider_ids = set(body.config.local_provider_ids + body.config.cloud_provider_ids
                           + body.config.independent_provider_ids)
        snapshots = {row["id"]: _provider_config_hash(row) for row in db.execute(
            f"SELECT * FROM runtime_providers WHERE id IN ({','.join('?' for _ in provider_ids)})",
            list(provider_ids),
        ).fetchall()} if provider_ids else {}
        if body.config.provider_config_hashes and body.config.provider_config_hashes != snapshots:
            raise HTTPException(409, "provider configuration changed; review a new profile")
        config["provider_config_hashes"] = snapshots
        digest = _sha({"name": body.name, "mode": body.mode, "config": config})
        profile_id = f"profile-{digest[:24]}"
        existing = db.execute("SELECT * FROM runtime_profiles WHERE immutable_hash=?", (digest,)).fetchone()
        if existing:
            return _profile_view(existing)
        db.execute("INSERT INTO runtime_profiles VALUES(?,?,?,?,?,?)",
                   (profile_id, body.name, body.mode, _dump(config), digest, now))
        _emit(db, profile_id, "profile.created", {"mode": body.mode})
    return get_profile(profile_id)


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: str):
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM runtime_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        raise HTTPException(404, "runtime profile not found")
    return _profile_view(row)


@router.get("/config")
def get_config():
    f = _core()
    with f.connect() as db:
        providers = db.execute("SELECT * FROM runtime_providers ORDER BY created_at,id").fetchall()
        profiles = db.execute("SELECT * FROM runtime_profiles ORDER BY created_at,id").fetchall()
    return {"providers": [_provider_view(row) for row in providers],
            "profiles": [_profile_view(row) for row in profiles]}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _assert_cloud_resolution(host: str, port: int) -> None:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except socket.gaierror as error:
        raise OSError("provider hostname resolution failed") from error
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise PermissionError("cloud provider resolved outside public address space")


def _health_request(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    base_url, kind = row["base_url"], row["kind"]
    if not base_url:
        return {"status": "unconfigured", "detail": "missing_endpoint"}
    parsed, location = urllib.parse.urlsplit(base_url), _location(row) if isinstance(row, sqlite3.Row) else row["location"]
    if location == "cloud":
        try:
            _assert_cloud_resolution(parsed.hostname or "", parsed.port or 443)
        except (OSError, PermissionError):
            return {"status": "unavailable", "detail": "unsafe_or_unresolved_endpoint"}
    endpoint = base_url.rstrip("/") + ("/api/tags" if kind == "ollama" else "/models")
    headers = {"Accept": "application/json"}
    secret_ref = row["secret_ref"] if "secret_ref" in row.keys() else None
    if secret_ref:
        try:
            secret = runtime_secrets.get(secret_ref)
        except Exception:
            return {"status": "unavailable", "detail": "secret_store_unavailable"}
        if not secret:
            return {"status": "unconfigured", "detail": "missing_secret"}
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            return {"status": "healthy" if 200 <= response.status < 300 else "degraded",
                    "http_status": response.status}
    except urllib.error.HTTPError as error:
        return {"status": "degraded", "http_status": error.code}
    except Exception as error:
        return {"status": "unavailable", "detail": type(error).__name__}


@router.post("/providers/test")
def test_provider(body: dict[str, Any]):
    provider_id = body.get("provider_id")
    if not isinstance(provider_id, str):
        raise HTTPException(422, "provider_id is required")
    f = _core()
    with f.connect() as db:
        row = db.execute("SELECT * FROM runtime_providers WHERE id=?", (provider_id,)).fetchone()
        if not row:
            raise HTTPException(404, "runtime provider not found")
    result = _health_request(row)
    now = _now()
    with f.connect() as db:
        db.execute("UPDATE runtime_providers SET last_health=?,last_health_at=?,updated_at=? WHERE id=?",
                   (result["status"], now, now, provider_id))
        _emit(db, provider_id, "provider.health", {"status": result["status"]})
    return result


@router.post("/local/detect")
def detect_local():
    values = []
    for kind, base_url in (("ollama", "http://127.0.0.1:11434"), ("llama_cpp", "http://127.0.0.1:8080")):
        result = _health_request({"kind": kind, "base_url": base_url, "location": "local"})
        values.append({"kind": kind, "base_url": base_url, **result})
    return {"local": values}


def _task_constraints(db: sqlite3.Connection, request: RouteRequest) -> tuple[str, dict[str, Any] | None]:
    sensitivity, task = request.sensitivity, None
    if request.task_id:
        task = db.execute("SELECT * FROM agent_tasks WHERE id=?", (request.task_id,)).fetchone()
        if not task:
            raise HTTPException(404, "agent task not found")
        if request.campaign_id and task["campaign_id"] != request.campaign_id:
            raise HTTPException(409, "task does not belong to the requested campaign")
        context = _load(task["context_capsule_json"], {})
        if context.get('native_discovery'):
            sensitivity = 'secret'
        if context.get("team_plan"):
            if context.get("cloud_context_approved") is not True:
                sensitivity = "secret"
            from v5_orchestration import _continuous_scope_current
            if not _continuous_scope_current(db, task):
                raise HTTPException(409, "team scope, policy or run changed")
        if context.get("continuous_research") or context.get("structured_worker"):
            from v5_orchestration import _continuous_scope_current
            if not _continuous_scope_current(db, task):
                raise HTTPException(409, "worker scope or policy changed")
            sensitivity = "secret"  # Continuous work stays local even if a caller requests cloud.
        elif _contains_sensitive_key(context):
            sensitivity = "secret"
        else:
            declared = context.get("sensitivity")
            order = {"public": 0, "internal": 1, "private": 2, "secret": 3}
            if declared in order and order[declared] > order[sensitivity]:
                sensitivity = declared
    return sensitivity, task


def _healthy_providers(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM runtime_providers WHERE enabled=1 AND last_health='healthy' ORDER BY created_at,id",
    ).fetchall()


def _ordered(rows: list[sqlite3.Row], complexity: float) -> list[sqlite3.Row]:
    desired = "small" if complexity < .65 else "large"
    return sorted(rows, key=lambda row: (
        0 if _load(row["metadata_json"], {}).get("tier") == desired else 1,
        _load(row["metadata_json"], {}).get("priority", 100), row["created_at"], row["id"],
    ))


def _provider_pool(rows: list[sqlite3.Row], location: str, configured: list[str], allow_unconfigured: bool = True) -> list[sqlite3.Row]:
    allowed = set(configured)
    return [row for row in rows if _location(row) == location
            and (row["id"] in allowed or (not allowed and allow_unconfigured))]


def _estimated_cost(row: sqlite3.Row, tokens: int) -> int:
    rate = int(_load(row["metadata_json"], {}).get("cost_micros_per_million_tokens", 0))
    return math.ceil(tokens * rate / 1_000_000)


def _used_cost(db: sqlite3.Connection, campaign_id: str | None) -> int:
    if not campaign_id:
        return 0
    return int(db.execute("SELECT COALESCE(SUM(cost_micros),0) FROM runtime_usage WHERE campaign_id=?",
                          (campaign_id,)).fetchone()[0])


@router.post("/routes", status_code=201)
def route_task(request: RouteRequest):
    f, now, decision_id = _core(), _now(), _uid("route")
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        sensitivity, task = _task_constraints(db, request)
        group_profile_id = None
        if task and task["group_id"]:
            group = db.execute("SELECT runtime_profile_id FROM research_groups WHERE id=?", (task["group_id"],)).fetchone()
            group_profile_id = group["runtime_profile_id"] if group else None
        if request.profile_id and group_profile_id and request.profile_id != group_profile_id:
            raise HTTPException(409, "task group runtime profile cannot be overridden")
        capsule_profile_id = _load(task["context_capsule_json"], {}).get("runtime_profile_id") if task else None
        if capsule_profile_id and request.profile_id and capsule_profile_id != request.profile_id:
            raise HTTPException(409, "task runtime profile cannot be overridden")
        if capsule_profile_id and group_profile_id and capsule_profile_id != group_profile_id:
            raise HTTPException(409, "task and group runtime profiles conflict")
        effective_profile_id = group_profile_id or capsule_profile_id or request.profile_id
        profile = db.execute("SELECT * FROM runtime_profiles WHERE id=?", (effective_profile_id,)).fetchone() if effective_profile_id else None
        if effective_profile_id and not profile:
            raise HTTPException(404, "runtime profile not found")
        mode = profile["mode"] if profile else (request.mode or "hybrid")
        config = ProfileConfig.model_validate(_load(profile["config_json"], {})) if profile else ProfileConfig()
        campaign_id = request.campaign_id or (task["campaign_id"] if task else None)
        max_tokens = min(request.estimated_tokens, config.max_tokens_per_call)
        if task:
            task_budget = _load(task["budget_json"], {})
            if task_budget.get("max_tokens") is not None:
                max_tokens = min(max_tokens, int(task_budget["max_tokens"]))
        used = _used_cost(db, campaign_id)
        budget_caps = [request.budget_remaining_micros]
        if config.max_cost_micros:
            budget_caps.append(max(0, config.max_cost_micros - used))
        if task:
            task_budget = _load(task["budget_json"], {})
            if task_budget.get("max_cost_micros") is not None:
                budget_caps.append(int(task_budget["max_cost_micros"]))
        available_budget = min(budget_caps) if budget_caps else 0
        healthy = _healthy_providers(db)
        changed_provider_ids = [row["id"] for row in healthy
                                if row["id"] in config.provider_config_hashes
                                and _provider_config_hash(row) != config.provider_config_hashes[row["id"]]]
        healthy = [row for row in healthy if row["id"] not in changed_provider_ids]
        bound_profile = bool(profile and "provider_config_hashes" in _load(profile["config_json"], {}))
        local = _ordered(_provider_pool(healthy, "local", config.local_provider_ids, not bound_profile), request.complexity)
        cloud = _ordered(_provider_pool(healthy, "cloud", config.cloud_provider_ids, not bound_profile), request.complexity)
        independent_ids = set(config.independent_provider_ids)
        independent = _ordered([
            row for row in healthy if _load(row["metadata_json"], {}).get("supports_independence")
            and (row["id"] in independent_ids or (not independent_ids and not bound_profile))
        ], request.complexity)
        reasons: list[str] = []
        provider: sqlite3.Row | None = None
        route = "blocked"
        if request.requires_independence:
            candidates = [row for row in independent
                          if (sensitivity not in {"private", "secret"} and mode not in {"local", "offline"})
                          or _location(row) == "local"]
            provider = candidates[0] if candidates else None
            route, reasons = ("independent", ["independence_required"]) if provider else ("blocked", ["no_healthy_independent_provider"])
        elif sensitivity in {"private", "secret"}:
            provider = local[0] if local else None
            route, reasons = ("local", ["sensitive_context_local"]) if provider else ("blocked", ["sensitive_context_requires_healthy_local_provider"])
        elif mode in {"local", "offline"}:
            provider = local[0] if local else None
            route, reasons = ("local", [f"{mode}_mode"]) if provider else ("blocked", [f"{mode}_mode_no_healthy_local_provider"])
        elif mode == "cloud":
            provider = cloud[0] if cloud else None
            if provider:
                route, reasons = "cloud", ["cloud_mode"]
            elif config.allow_fallback and local:
                provider, route, reasons = local[0], "local", ["cloud_unavailable_fallback_local"]
            else:
                reasons = ["cloud_mode_no_healthy_provider"]
        else:
            prefer_local = request.complexity < config.cloud_complexity_threshold or available_budget <= 0
            primary, fallback = (local, cloud) if prefer_local else (cloud, local)
            if primary:
                provider = primary[0]
                route = _location(provider)
                reasons = ["low_complexity_local_first" if prefer_local and available_budget > 0 else
                           "cloud_budget_exhausted_local" if prefer_local else "high_complexity_cloud"]
            elif config.allow_fallback and fallback:
                provider = fallback[0]
                route = _location(provider)
                reasons = ["preferred_provider_unavailable_fallback"]
            else:
                reasons = ["no_healthy_provider_for_hybrid_route"]
        if changed_provider_ids:
            reasons.append("profile_provider_configuration_changed")
        if provider:
            max_tokens = min(max_tokens, int(_load(provider["metadata_json"], {}).get("max_context_tokens", max_tokens)))
        estimated_cost = _estimated_cost(provider, max_tokens) if provider and _location(provider) == "cloud" else 0
        if provider and _location(provider) == "cloud" and (available_budget <= 0 or estimated_cost > available_budget):
            independent_local = [row for row in independent if _location(row) == "local"]
            fallback_pool = independent_local if request.requires_independence else local
            if config.allow_fallback and fallback_pool:
                provider = fallback_pool[0]
                route = "independent" if request.requires_independence else "local"
                reasons.append("cloud_budget_preflight_fallback_local")
                estimated_cost = 0
            else:
                provider, route = None, "blocked"
                reasons.append("cloud_budget_preflight_blocked")
        status = "selected" if provider else "blocked"
        max_cost = min(available_budget, estimated_cost) if provider and route in {"cloud", "independent"} and _location(provider) == "cloud" else 0
        safe_request = request.model_dump(mode="json") | {"effective_sensitivity": sensitivity,
            "provider_configuration_sha256": _provider_config_hash(provider) if provider else None}
        db.execute(
            "INSERT INTO runtime_route_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (decision_id, campaign_id, request.task_id, effective_profile_id, _dump(safe_request), route,
             provider["id"] if provider else None, provider["model"] if provider else None,
             _dump(reasons), max_tokens, max_cost, status, now),
        )
        _emit(db, decision_id, "route.decided", {"route": route, "provider_id": provider["id"] if provider else None,
                                                  "reason": reasons}, campaign_id)
    return {"id": decision_id, "status": status, "mode": mode, "profile_id": effective_profile_id, "route": route,
            "provider_ref": provider["id"] if provider else None, "model": provider["model"] if provider else None,
            "reason": reasons, "max_tokens": max_tokens, "max_cost_micros": max_cost,
            "estimated_cost_micros": estimated_cost, "effective_sensitivity": sensitivity,
            "provider_configuration_sha256": _provider_config_hash(provider) if provider else None}


@router.get("/routes")
def list_routes(campaign_id: str | None = None, limit: int = Query(100, ge=1, le=500),
                offset: int = Query(0, ge=0)):
    f = _core()
    where, params = (" WHERE campaign_id=?", [campaign_id]) if campaign_id else ("", [])
    with f.connect() as db:
        total = db.execute("SELECT COUNT(*) FROM runtime_route_decisions" + where, params).fetchone()[0]
        rows = db.execute("SELECT * FROM runtime_route_decisions" + where +
                          " ORDER BY created_at DESC,id LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
    items = []
    for row in rows:
        value = dict(row)
        value["request"] = _load(value.pop("request_json"), {})
        value["reason"] = _load(value.pop("reason_json"), [])
        items.append(value)
    return {"items": items, "page": {"limit": limit, "offset": offset, "total": total,
                                      "has_more": offset + len(items) < total}}


@router.post("/usage", status_code=201)
def record_usage(body: UsageReport):
    payload = body.model_dump(mode="json")
    if body.call_id is None:
        payload.pop("call_id", None)
    if body.runtime_ms is None:
        payload.pop("runtime_ms", None)
    digest, f, now = _sha(payload), _core(), _now()
    with f.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM runtime_usage_reports WHERE idempotency_key=?",
                              (body.idempotency_key,)).fetchone()
        if existing:
            if existing["payload_sha256"] != digest:
                raise HTTPException(409, "usage idempotency key was reused with a different report")
            usage = db.execute("SELECT * FROM runtime_usage WHERE id=?", (existing["usage_id"],)).fetchone()
            return _usage_value(usage, db)
        decision = db.execute("SELECT * FROM runtime_route_decisions WHERE id=?", (body.decision_id,)).fetchone()
        if not decision or decision["status"] != "selected" or not decision["provider_id"]:
            raise HTTPException(409, "usage requires a selected runtime route decision")
        call = db.execute("SELECT * FROM runtime_calls WHERE decision_id=?", (body.decision_id,)).fetchone()
        if call and (body.call_id != call["id"] or call["state"] not in {"calling", "unknown"}):
            raise HTTPException(409, "usage must settle the reserved model call")
        if body.call_id and not call:
            raise HTTPException(409, "model call reservation not found")
        if body.runtime_ms is not None and not call:
            raise HTTPException(409, "model timing requires a call reservation")
        selected_provider = db.execute("SELECT * FROM runtime_providers WHERE id=?", (decision["provider_id"],)).fetchone()
        if selected_provider and _location(selected_provider) == "local" and body.cost_micros:
            raise HTTPException(422, "local route usage cannot report cloud cost")
        cursor = db.execute(
            "INSERT INTO runtime_usage(campaign_id,task_id,provider_id,route,input_tokens,output_tokens,cost_micros,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (decision["campaign_id"], decision["task_id"], decision["provider_id"], decision["route"],
             body.input_tokens, body.output_tokens, body.cost_micros, now),
        )
        usage_id = cursor.lastrowid
        if call:
            db.execute("UPDATE runtime_calls SET state='settled',usage_id=?,updated_at=? WHERE id=?",
                       (usage_id, now, call["id"]))
            if body.runtime_ms is not None:
                prior = db.execute("SELECT COALESCE(SUM(COALESCE(t.runtime_ms,c.max_runtime_ms)),0),"
                                   "COALESCE(MAX(t.legacy_runtime_ms),0) FROM runtime_calls c "
                                   "LEFT JOIN runtime_call_timings t ON t.call_id=c.id "
                                   "WHERE c.task_id=? AND c.id!=? AND c.state!='released'",
                                   (call['task_id'], call['id'])).fetchone()
                legacy = db.execute('SELECT runtime_ms FROM agent_task_usage WHERE task_id=?', (call['task_id'],)).fetchone()
                baseline = max(prior[1], (legacy[0] if legacy else 0) - prior[0])
                db.execute("INSERT INTO runtime_call_timings VALUES(?,?,?,?)", (call["id"], body.runtime_ms, baseline, now))
        db.execute("INSERT INTO runtime_usage_reports VALUES(?,?,?,?,?)",
                   (body.idempotency_key, body.decision_id, usage_id, digest, now))
        _emit(db, str(usage_id), "usage.recorded",
              {"decision_id": body.decision_id, "route": decision["route"], "cost_micros": body.cost_micros},
              decision["campaign_id"])
        usage = db.execute("SELECT * FROM runtime_usage WHERE id=?", (usage_id,)).fetchone()
        return _usage_value(usage, db)


def _usage_value(row: sqlite3.Row, db: sqlite3.Connection) -> dict[str, Any]:
    report = db.execute("SELECT decision_id FROM runtime_usage_reports WHERE usage_id=?", (row["id"],)).fetchone()
    decision = db.execute("SELECT max_cost_micros FROM runtime_route_decisions WHERE id=?",
                          (report["decision_id"],)).fetchone()
    value = dict(row)
    value["decision_id"] = report["decision_id"]
    value["over_budget"] = value["cost_micros"] > decision["max_cost_micros"]
    return value


@router.get("/calls")
def list_model_calls(campaign_id: str | None = None, limit: int = Query(50, ge=1, le=500),
                     offset: int = Query(0, ge=0)):
    from v5_runtime_calls import recover_calls
    where, params = (" WHERE campaign_id=?", [campaign_id]) if campaign_id else ("", [])
    with _core().connect() as db:
        db.execute("BEGIN IMMEDIATE")
        recover_calls(db)
        total = db.execute("SELECT COUNT(*) FROM runtime_calls" + where, params).fetchone()[0]
        rows = db.execute("SELECT c.*,p.strategy input_counting,p.input_tokens preflight_input_tokens,"
                          "p.output_max_tokens FROM runtime_calls c LEFT JOIN runtime_call_inputs p ON p.call_id=c.id"
                          + where + " ORDER BY c.created_at DESC,c.id LIMIT ? OFFSET ?",
                          [*params, limit, offset]).fetchall()
        states = db.execute("SELECT state,COUNT(*) count,SUM(reserved_tokens) tokens,SUM(reserved_cost_micros) cost "
                            "FROM runtime_calls" + where + " GROUP BY state", params).fetchall()
    return {"items": [dict(row) for row in rows], "page": {"total": total, "limit": limit,
            "offset": offset, "has_more": offset + len(rows) < total},
            "states": {row["state"]: {"count": row["count"], "reserved_tokens": row["tokens"],
                                      "reserved_cost_micros": row["cost"]} for row in states}}


@router.get("/usage")
def runtime_usage(campaign_id: str | None = None):
    f = _core()
    where, params = (" WHERE campaign_id=?", (campaign_id,)) if campaign_id else ("", ())
    with f.connect() as db:
        rows = db.execute(
            "SELECT route,provider_id,SUM(input_tokens) input_tokens,SUM(output_tokens) output_tokens,"
            "SUM(cost_micros) cost_micros,COUNT(*) calls FROM runtime_usage" + where +
            " GROUP BY route,provider_id ORDER BY route,provider_id", params,
        ).fetchall()
    items = [dict(row) for row in rows]
    return {"items": items, "total": {
        "input_tokens": sum(item["input_tokens"] or 0 for item in items),
        "output_tokens": sum(item["output_tokens"] or 0 for item in items),
        "cost_micros": sum(item["cost_micros"] or 0 for item in items),
        "calls": sum(item["calls"] for item in items),
    }}


def status_snapshot() -> dict[str, Any]:
    f = _core()
    with f.connect() as db:
        rows = db.execute("SELECT * FROM runtime_providers ORDER BY created_at,id").fetchall()
        profiles = db.execute("SELECT COUNT(*) FROM runtime_profiles").fetchone()[0]
        decisions = db.execute("SELECT COUNT(*) FROM runtime_route_decisions").fetchone()[0]
    healthy_local = sum(1 for row in rows if row["enabled"] and row["last_health"] == "healthy" and _location(row) == "local")
    healthy_cloud = sum(1 for row in rows if row["enabled"] and row["last_health"] == "healthy" and _location(row) == "cloud")
    return {"providers": [_provider_view(row) for row in rows], "profiles": profiles, "decisions": decisions,
            "healthy": {"local": healthy_local, "cloud": healthy_cloud},
            "modes": {"local": healthy_local > 0, "cloud": healthy_cloud > 0,
                      "hybrid": healthy_local > 0 and healthy_cloud > 0, "offline": healthy_local > 0}}
