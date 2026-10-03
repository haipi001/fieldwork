import base64
import json

import final_core
from tests.test_final import client
from tests.test_v5_orchestration import campaign, task


def test_task_cursor_reaches_all_tied_records_and_binds_filters(client):
    cid = campaign(client)["id"]
    first = task(client, cid, "pagination-seed")
    with final_core.connect() as db:
        template = dict(db.execute("SELECT * FROM agent_tasks WHERE id=?", (first["id"],)).fetchone())
        for index in range(1000):
            row = {**template, "id": f"page-task-{index:04d}", "idempotency_key": f"page-key-{index:04d}",
                   "priority": index % 3}
            db.execute(f"INSERT INTO agent_tasks ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    params = {"campaign_id": cid, "status": "queued", "limit": 500}
    first_page = client.get("/api/v1/orchestration/tasks", params=params).json()
    cursor = first_page["page"]["next_cursor"]
    assert first_page["page"]["has_more"] is True and len(first_page["items"]) == 500
    late = task(client, cid, "inserted-ahead-of-cursor", priority=10)
    ids = [row["id"] for row in first_page["items"]]
    for _ in range(2):
        response = client.get("/api/v1/orchestration/tasks", params={**params, "cursor": cursor})
        assert response.status_code == 200, response.text
        page = response.json()
        ids.extend(row["id"] for row in page["items"])
        cursor = page["page"]["next_cursor"]
    assert cursor is None and len(ids) == len(set(ids)) == 1001
    with final_core.connect() as db:
        expected = [row[0] for row in db.execute("SELECT id FROM agent_tasks WHERE campaign_id=? AND id<>? ORDER BY priority DESC,created_at,id", (cid, late["id"]))]
    assert ids == expected
    assert client.get("/api/v1/orchestration/tasks", params=params).json()["items"][0]["id"] == late["id"]
    saved = first_page["page"]["next_cursor"]
    for invalid in ({"campaign_id": "other"}, {"status": "running"}, {"cursor": "bad-token"}):
        assert client.get("/api/v1/orchestration/tasks", params={**params, "cursor": saved, **invalid}).status_code == 422
    nan = base64.urlsafe_b64encode(json.dumps([cid, "queued", float("nan"), "date", "id"]).encode()).decode()
    assert client.get("/api/v1/orchestration/tasks", params={**params, "cursor": nan}).status_code == 422
