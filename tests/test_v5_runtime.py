import json
import os
import sqlite3

import final_core
import runtime_secrets
import v5_runtime
from tests.test_final import client, create_ready


def provider(client, name, location, *, independent=False, rate=1_000_000, key=None, enabled=True):
    base = "http://127.0.0.1:11434" if location == "local" else f"https://{name}.example.test/v1"
    response = client.post("/api/v1/runtime/providers", json={
        "kind": "ollama" if location == "local" else "openai_compatible",
        "name": name, "location": location, "base_url": base, "model": f"{name}-model",
        "api_key": key, "enabled": enabled,
        "metadata": {
            "priority": 10, "tier": "verifier" if independent else "small",
            "supports_independence": independent,
            "cost_micros_per_million_tokens": 0 if location == "local" else rate,
            "max_context_tokens": 65536,
        },
    })
    assert response.status_code == 201, response.text
    return response.json()


def healthy(provider_id, state="healthy"):
    with final_core.connect() as db:
        db.execute("UPDATE runtime_providers SET last_health=?,last_health_at=? WHERE id=?",
                   (state, final_core.utcnow(), provider_id))


def profile(client, mode, *, local=None, cloud=None, independent=None, fallback=True, budget=5_000_000):
    response = client.put("/api/v1/runtime/config", json={
        "name": f"{mode}-profile", "mode": mode,
        "config": {
            "local_provider_ids": local or [], "cloud_provider_ids": cloud or [],
            "independent_provider_ids": independent or [], "allow_fallback": fallback,
            "cloud_complexity_threshold": .65, "max_tokens_per_call": 32000,
            "max_cost_micros": budget,
        },
    })
    assert response.status_code == 201, response.text
    return response.json()


def route(client, profile_id, **changes):
    body = {
        "profile_id": profile_id, "task_type": "hypothesis_exploration",
        "sensitivity": "internal", "complexity": .8, "estimated_tokens": 10000,
        "budget_remaining_micros": 1_000_000,
    }
    body.update(changes)
    response = client.post("/api/v1/runtime/routes", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_provider_secret_is_reference_only_owner_only_and_never_returned(client, monkeypatch, tmp_path):
    root = tmp_path / "runtime-secrets"
    monkeypatch.setattr(runtime_secrets, "secret_root", lambda: root)
    secret = "provider-secret-never-returned"
    created = provider(client, "cloud-a", "cloud", key=secret)
    assert created["secret_configured"] is True
    assert secret not in json.dumps(created) and "secret_ref" not in created
    files = list(root.iterdir())
    assert len(files) == 1 and files[0].stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700 and files[0].read_text() == secret
    with final_core.connect() as db:
        row = db.execute("SELECT * FROM runtime_providers WHERE id=?", (created["id"],)).fetchone()
        assert row["secret_ref"] and secret not in "|".join(str(value) for value in row)
    config = client.get("/api/v1/runtime/config")
    status = client.get("/api/v1/runtime/status")
    assert secret not in config.text and secret not in status.text
    assert "secret_ref" not in config.text and "secret_ref" not in status.text
    rotated = client.patch(f"/api/v1/runtime/providers/{created['id']}", json={
        "api_key": "rotated-provider-secret", "enabled": False,
    })
    assert rotated.status_code == 200 and rotated.json()["enabled"] is False
    assert "rotated-provider-secret" not in rotated.text and files[0].read_text() == "rotated-provider-secret"
    cleared = client.patch(f"/api/v1/runtime/providers/{created['id']}", json={"clear_secret": True})
    assert cleared.status_code == 200 and cleared.json()["secret_configured"] is False
    assert not files[0].exists()


def test_cloud_local_hybrid_and_offline_routes_are_health_and_budget_aware(client):
    local = provider(client, "local-a", "local")
    cloud = provider(client, "cloud-a", "cloud")
    healthy(local["id"]); healthy(cloud["id"])
    local_profile = profile(client, "local", local=[local["id"]])
    cloud_profile = profile(client, "cloud", local=[local["id"]], cloud=[cloud["id"]])
    hybrid_profile = profile(client, "hybrid", local=[local["id"]], cloud=[cloud["id"]])
    offline_profile = profile(client, "offline", local=[local["id"]])
    assert route(client, local_profile["id"])["route"] == "local"
    assert route(client, cloud_profile["id"])["route"] == "cloud"
    assert route(client, hybrid_profile["id"], complexity=.2)["route"] == "local"
    assert route(client, hybrid_profile["id"], complexity=.9)["route"] == "cloud"
    offline = route(client, offline_profile["id"])
    assert offline["route"] == "local" and offline["reason"] == ["offline_mode"]
    budget_stop = route(client, hybrid_profile["id"], complexity=.9, budget_remaining_micros=0)
    assert budget_stop["route"] == "local" and "cloud_budget_exhausted_local" in budget_stop["reason"]
    healthy(cloud["id"], "unavailable")
    fallback = route(client, cloud_profile["id"])
    assert fallback["route"] == "local" and fallback["reason"] == ["cloud_unavailable_fallback_local"]


def test_sensitive_task_never_routes_cloud_and_no_local_fails_closed(client):
    local = provider(client, "local-sensitive", "local")
    cloud = provider(client, "cloud-sensitive", "cloud")
    healthy(local["id"]); healthy(cloud["id"])
    configured = profile(client, "cloud", local=[local["id"]], cloud=[cloud["id"]])
    sensitive = route(client, configured["id"], sensitivity="secret")
    assert sensitive["route"] == "local" and sensitive["provider_ref"] == local["id"]
    healthy(local["id"], "unavailable")
    blocked = route(client, configured["id"], sensitivity="private")
    assert blocked["status"] == "blocked" and blocked["provider_ref"] is None
    assert "requires_healthy_local" in blocked["reason"][0]

    engagement = create_ready(client, target="https://runtime-task.example.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Runtime task", "objective": "Route sensitive work",
    }).json()
    task = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": campaign["id"], "role": "researcher", "objective": "Use private source",
        "context_capsule": {"api_key": "not-returned", "sensitivity": "public"},
        "idempotency_key": "runtime-sensitive-task",
    }).json()
    healthy(local["id"])
    derived = route(client, configured["id"], sensitivity="public", task_id=task["id"],
                    campaign_id=campaign["id"])
    assert derived["effective_sensitivity"] == "secret" and derived["route"] == "local"
    listed = client.get("/api/v1/runtime/routes").text
    assert "not-returned" not in listed and "api_key" not in listed


def test_independent_route_never_falls_back_to_non_independent_provider(client):
    ordinary = provider(client, "ordinary-local", "local")
    verifier = provider(client, "independent-cloud", "cloud", independent=True, rate=5_000_000)
    healthy(ordinary["id"]); healthy(verifier["id"])
    current = profile(client, "hybrid", local=[ordinary["id"]], cloud=[verifier["id"]],
                      independent=[verifier["id"]])
    blocked = route(client, current["id"], requires_independence=True, budget_remaining_micros=0)
    assert blocked["status"] == "blocked" and blocked["provider_ref"] is None
    assert "cloud_budget_preflight_blocked" in blocked["reason"]
    allowed = route(client, current["id"], requires_independence=True, budget_remaining_micros=1_000_000)
    assert allowed["route"] == "independent" and allowed["provider_ref"] == verifier["id"]


def test_usage_is_idempotent_immutable_and_future_routes_observe_campaign_budget(client):
    cloud = provider(client, "metered-cloud", "cloud", rate=1_000_000)
    healthy(cloud["id"])
    current = profile(client, "cloud", cloud=[cloud["id"]], fallback=False, budget=1000)
    engagement = create_ready(client, target="https://usage.example.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Usage", "objective": "Track model cost",
    }).json()
    decision = route(client, current["id"], campaign_id=campaign["id"], estimated_tokens=500,
                     budget_remaining_micros=1000)
    report = {
        "decision_id": decision["id"], "idempotency_key": "usage-once",
        "input_tokens": 400, "output_tokens": 100, "cost_micros": 500,
    }
    first = client.post("/api/v1/runtime/usage", json=report)
    second = client.post("/api/v1/runtime/usage", json=report)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"] and first.json()["over_budget"] is False
    changed = client.post("/api/v1/runtime/usage", json={**report, "cost_micros": 501})
    assert changed.status_code == 409
    summary = client.get(f"/api/v1/runtime/usage?campaign_id={campaign['id']}").json()
    assert summary["total"] == {"input_tokens": 400, "output_tokens": 100, "cost_micros": 500, "calls": 1}
    next_decision = route(client, current["id"], campaign_id=campaign["id"], estimated_tokens=600,
                          budget_remaining_micros=1000)
    assert next_decision["status"] == "blocked" and "cloud_budget_preflight_blocked" in next_decision["reason"]
    with final_core.connect() as db:
        for statement, identifier in (
            ("UPDATE runtime_route_decisions SET route='local' WHERE id=?", decision["id"]),
            ("DELETE FROM runtime_usage WHERE id=?", first.json()["id"]),
            ("UPDATE runtime_profiles SET name='changed' WHERE id=?", current["id"]),
        ):
            try:
                db.execute(statement, (identifier,))
            except sqlite3.IntegrityError as error:
                assert "immutable" in str(error)
            else:
                raise AssertionError(f"immutable statement succeeded: {statement}")


def test_provider_url_and_profile_contracts_fail_closed(client):
    for body in (
        {"name": "bad-cloud", "location": "cloud", "base_url": "http://api.example.test", "model": "m"},
        {"name": "bad-local", "location": "local", "base_url": "http://192.168.1.8:11434", "model": "m"},
        {"name": "no-port", "location": "local", "base_url": "http://127.0.0.1", "model": "m"},
        {"name": "credential-url", "location": "cloud", "base_url": "https://user:pass@example.test", "model": "m"},
    ):
        assert client.post("/api/v1/runtime/providers", json=body).status_code == 422
    local = provider(client, "profile-local", "local")
    mismatch = client.put("/api/v1/runtime/config", json={
        "name": "Mismatch", "mode": "cloud", "config": {"cloud_provider_ids": [local["id"]]},
    })
    assert mismatch.status_code == 409


def test_agent_task_inherits_immutable_group_runtime_profile(client):
    local = provider(client, "group-local", "local")
    cloud = provider(client, "group-cloud", "cloud")
    healthy(local["id"]); healthy(cloud["id"])
    local_profile = profile(client, "local", local=[local["id"]])
    cloud_profile = profile(client, "cloud", cloud=[cloud["id"]])
    engagement = create_ready(client, target="https://profile-inheritance.example.test")
    campaign = client.post(f"/api/v1/engagements/{engagement['id']}/campaigns", json={
        "name": "Profile inheritance", "objective": "Bind group routing",
    }).json()
    group = client.post("/api/v1/orchestration/groups", json={
        "campaign_id": campaign["id"], "role": "research", "objective": "Use local profile",
        "runtime_profile_id": local_profile["id"],
    })
    assert group.status_code == 201, group.text
    task = client.post("/api/v1/orchestration/tasks", json={
        "campaign_id": campaign["id"], "group_id": group.json()["id"], "role": "researcher",
        "objective": "Inherited route", "idempotency_key": "inherited-route",
    }).json()
    inherited = client.post("/api/v1/runtime/routes", json={
        "task_type": "triage", "task_id": task["id"], "campaign_id": campaign["id"],
        "estimated_tokens": 1000, "budget_remaining_micros": 1000,
    })
    assert inherited.status_code == 201
    assert inherited.json()["profile_id"] == local_profile["id"] and inherited.json()["route"] == "local"
    override = client.post("/api/v1/runtime/routes", json={
        "task_type": "triage", "task_id": task["id"], "campaign_id": campaign["id"],
        "profile_id": cloud_profile["id"], "estimated_tokens": 1000, "budget_remaining_micros": 1000,
    })
    assert override.status_code == 409
    missing_group = client.post("/api/v1/orchestration/groups", json={
        "campaign_id": campaign["id"], "role": "research", "objective": "Invalid profile",
        "runtime_profile_id": "profile-missing",
    })
    assert missing_group.status_code == 404
