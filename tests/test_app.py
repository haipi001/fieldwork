import tempfile
import sqlite3
from pathlib import Path

import app
import adapters
import pytest


def test_scope_decision_allows_exact_host():
    manifest = {"allowed_hosts": ["demo.local"], "denied_hosts": [], "allowed_methods": ["GET"], "allow_state_change": False}
    assert app.scope_decision(manifest, "https://demo.local/api", "GET")[0] is True


def test_scope_decision_rejects_subdomain_and_denied():
    manifest = {"allowed_hosts": ["demo.local"], "denied_hosts": ["pay.demo.local"], "allowed_methods": ["GET"]}
    assert app.scope_decision(manifest, "https://evil.demo.local", "GET")[0] is False
    assert app.scope_decision(manifest, "https://pay.demo.local", "GET")[0] is False


def test_scope_decision_blocks_state_change():
    manifest = {"allowed_hosts": ["demo.local"], "denied_hosts": [], "allowed_methods": ["POST"], "allow_state_change": False}
    assert app.scope_decision(manifest, "https://demo.local/api", "POST")[0] is False


def test_manifest_defaults_preserve_safe_mode():
    manifest = app.parse_manifest("engagement_id: unit-1\nprogram: Unit\nallowed_hosts: [unit.local]\nallowed_methods: [GET]\n")
    assert manifest["allow_state_change"] is False
    assert manifest["allow_oast"] is False
    assert manifest["max_requests"] == 100
    assert manifest["allowed_ports"] == [80, 443]
    assert manifest["allowed_paths"] == ["/"]


def test_scope_decision_enforces_port_and_path_boundaries():
    manifest = {
        "allowed_hosts": ["demo.local"], "denied_hosts": [], "allowed_methods": ["GET"],
        "allowed_ports": [443], "allowed_paths": ["/api"], "denied_paths": ["/api/admin"],
    }
    assert app.scope_decision(manifest, "https://demo.local/api/profile", "GET")[0] is True
    assert app.scope_decision(manifest, "https://demo.local:8443/api/profile", "GET")[0] is False
    assert app.scope_decision(manifest, "https://demo.local/apiv2", "GET")[0] is False
    assert app.scope_decision(manifest, "https://demo.local/api/admin/users", "GET")[0] is False


def test_scope_decision_rejects_embedded_credentials_and_expired_grant():
    base = {"allowed_hosts": ["demo.local"], "allowed_methods": ["GET"]}
    assert app.scope_decision(base, "https://user:pass@demo.local/", "GET")[0] is False
    expired = {**base, "valid_until": "2020-01-01"}
    assert app.scope_decision(expired, "https://demo.local/", "GET")[0] is False


def test_adapter_command_is_argv_only_and_expands_known_fields(monkeypatch):
    monkeypatch.setenv("SRC_STRIX_COMMAND", "python3 runner.py --target {target} --engagement {engagement_id}")
    args = adapters.build_command("strix", "src-1", "https://demo.local/api")
    assert args == ["python3", "runner.py", "--target", "https://demo.local/api", "--engagement", "src-1"]
    assert all(";" not in item for item in args)


def test_unconfigured_real_adapters_fail_closed(monkeypatch):
    monkeypatch.delenv("SRC_STRIX_COMMAND", raising=False)
    monkeypatch.delenv("SRC_PENTEST_AI_COMMAND", raising=False)
    statuses = {item.name: item for item in adapters.adapter_statuses()}
    assert statuses["demo"].available is True
    assert statuses["strix"].configured is False
    assert statuses["pentest-ai"].configured is False


def test_report_is_verified_only_and_redacts_secrets():
    finding = {
        "title": "Verified test", "severity": "medium", "status": "verified",
        "target": "https://demo.local/api", "oracle": "test-oracle", "replay_count": 2,
        "evidence": '{"authorization":"Bearer abc.def", "token":"secret-value"}',
        "counterevidence": '{"negative_control":"cookie=session-secret"}',
    }
    report = app.build_report("Demo", [finding])
    assert "abc.def" not in report
    assert "secret-value" not in report
    assert "[REDACTED]" in report


def test_report_rejects_candidate_only_input():
    with pytest.raises(app.HTTPException) as error:
        app.build_report("Demo", [{"status": "candidate"}])
    assert error.value.status_code == 409


def test_target_normalization_collapses_object_ids():
    left = app.normalize_target("https://Demo.Local/api/users/123e4567-e89b-12d3-a456-426614174000?debug=1")
    right = app.normalize_target("https://demo.local/api/users/987e6543-e21b-12d3-a456-426614174999")
    assert left == right == "https://demo.local/api/users/{id}"


def test_finding_fingerprint_is_stable_and_category_sensitive():
    a = app.finding_fingerprint("access_control", "https://demo.local/api/users/123e4567-e89b", "IDOR candidate")
    b = app.finding_fingerprint("access_control", "https://demo.local/api/users/987e6543-e21b", "IDOR candidate")
    c = app.finding_fingerprint("injection", "https://demo.local/api/users/987e6543-e21b", "IDOR candidate")
    assert a == b
    assert a != c


def test_evidence_ledger_detects_tampering():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript("""
      CREATE TABLE evidence_ledger(id TEXT PRIMARY KEY,finding_id TEXT,kind TEXT,content TEXT,sha256 TEXT,source TEXT,created_at TEXT,UNIQUE(finding_id,kind));
      CREATE TABLE counterevidence_ledger(id TEXT PRIMARY KEY,finding_id TEXT,kind TEXT,content TEXT,sha256 TEXT,source TEXT,created_at TEXT,UNIQUE(finding_id,kind));
    """)
    for kind in ("baseline", "attack", "sha256"):
        app.add_ledger_entry(db, "evidence_ledger", "f-1", kind, f"content-{kind}", "unit")
    app.add_ledger_entry(db, "counterevidence_ledger", "f-1", "negative_control", "control", "unit")
    assert app.verify_ledger(db, "f-1")["valid"] is True
    db.execute("UPDATE evidence_ledger SET content='tampered' WHERE kind='attack'")
    result = app.verify_ledger(db, "f-1")
    assert result["valid"] is False
    assert len(result["tampered_entries"]) == 1


def test_ip_policy_blocks_private_loopback_and_reserved_by_default():
    manifest = {"allow_private_ips": False}
    assert app.ip_policy_decision(manifest, ["127.0.0.1"])[0] is False
    assert app.ip_policy_decision(manifest, ["10.0.0.8"])[0] is False
    assert app.ip_policy_decision(manifest, ["::1"])[0] is False
    assert app.ip_policy_decision(manifest, ["8.8.8.8"])[0] is True


def test_ip_policy_requires_explicit_private_lab_opt_in():
    manifest = {"allow_private_ips": True}
    assert app.ip_policy_decision(manifest, ["127.0.0.1", "10.0.0.8"])[0] is True
    assert app.ip_policy_decision(manifest, ["not-an-ip"])[0] is False


def test_redirect_hops_reuse_full_scope_policy():
    manifest = {"allowed_hosts": ["demo.local"], "allowed_methods": ["GET"], "allowed_paths": ["/api"]}
    assert app.scope_decision(manifest, "https://demo.local/api/start", "GET")[0] is True
    assert app.scope_decision(manifest, "https://oauth.third-party.test/callback", "GET")[0] is False


def test_budget_controller_consumes_atomically_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "DB", tmp_path / "budget.db")
    app.init_db()
    with app.connect() as db:
        db.execute("INSERT INTO engagements VALUES(?,?,?,?,?,?)", ("e-1", "Unit", "confirmed", "{}", app.now(), app.now()))
        db.execute("INSERT INTO runs(id,engagement_id,status,request_limit,adapter) VALUES(?,?,?,?,?)", ("r-1", "e-1", "running", 2, "demo"))
        db.execute("INSERT INTO budgets VALUES(?,?,?,?,?,?,?,?)", ("r-1", 2, 0, 1, 0, 100, 0, app.now()))
    assert app.consume_budget("r-1", "request", 1)[0] is True
    assert app.consume_budget("r-1", "request", 1)[0] is True
    assert app.consume_budget("r-1", "request", 1)[0] is False
    assert app.consume_budget("r-1", "tool_call", 1)[0] is True
    assert app.consume_budget("r-1", "tool_call", 1)[0] is False
    assert app.consume_budget("r-1", "model_cost_micros", 100)[0] is True
    assert app.consume_budget("r-1", "model_cost_micros", 1)[0] is False
    with app.connect() as db:
        budget = db.execute("SELECT * FROM budgets WHERE run_id='r-1'").fetchone()
        run = db.execute("SELECT requests_used FROM runs WHERE id='r-1'").fetchone()
    assert budget["requests_used"] == 2
    assert budget["tool_calls_used"] == 1
    assert budget["model_cost_micros"] == 100
    assert run["requests_used"] == 2
