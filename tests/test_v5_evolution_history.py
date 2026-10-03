"""Long lineage reads remain bounded and revalidate ancestor state."""
import json

import final_core
from tests.test_final import client
from tests.test_v5_evolution_recovery import seed


def history(client, count):
    cid, group, initial = seed(client)
    with final_core.connect() as db:
        population = dict(db.execute("SELECT * FROM research_populations_v5 WHERE id=?", (initial["id"],)).fetchone())
        variants = [dict(row) for row in db.execute("SELECT * FROM research_variants_v5 WHERE population_id=? ORDER BY rank", (initial["id"],))]
        previous = initial["id"]
        previous_variants = [row["id"] for row in variants]
        for generation in range(1, count):
            pid = f"history-population-{generation:06d}"
            value = {**population, "id": pid, "generation": generation,
                     "parent_population_id": previous, "request_key": f"history-{generation}"}
            db.execute(f"INSERT INTO research_populations_v5 ({','.join(value)}) VALUES ({','.join('?' for _ in value)})", tuple(value.values()))
            next_variants = []
            for index, variant in enumerate(variants):
                vid = f"history-variant-{generation:06d}-{index}"
                value = {**variant, "id": vid, "population_id": pid,
                         "parent_variant_ids_json": json.dumps([previous_variants[index]])}
                db.execute(f"INSERT INTO research_variants_v5 ({','.join(value)}) VALUES ({','.join('?' for _ in value)})", tuple(value.values()))
                next_variants.append(vid)
            previous, previous_variants = pid, next_variants
    return cid, initial, previous


def test_long_lineage_reads_are_iterative_and_linear_per_request(client, monkeypatch):
    count = 1100
    cid, initial, latest = history(client, count)
    original = final_core.connect
    queries = []

    def traced_connection():
        db = original()
        db.set_trace_callback(queries.append)
        return db

    monkeypatch.setattr(final_core, "connect", traced_connection)
    response = client.get(f"/api/v1/evolution/populations/{latest}")
    assert response.status_code == 200 and response.json()["current"] is True
    queries.clear()
    response = client.get(f"/api/v1/evolution/populations?campaign_id={cid}&limit=500")
    assert response.status_code == 200 and len(response.json()["items"]) == 500
    assert all(item["current"] for item in response.json()["items"])
    assert len(queries) < count * 16, len(queries)
    # Request-local caching must not hide a later change to an ancestor input.
    assert client.patch(f"/api/v1/research/nodes/{initial['variants'][0]['claim_node_id']}",
                        json={"title": "Changed initial claim invalidates descendant snapshots"}).status_code == 200
    assert client.get(f"/api/v1/evolution/populations/{latest}").json()["current"] is False


def test_cyclic_lineage_fails_closed(client):
    _, initial, latest = history(client, 4)
    # Model a legacy/corrupt archive without changing ordinary write validation.
    with final_core.connect() as db:
        triggers = db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='research_populations_v5'").fetchall()
        for trigger in triggers:
            db.execute('DROP TRIGGER "' + trigger['name'].replace('"', '""') + '"')
        db.execute("UPDATE research_populations_v5 SET parent_population_id=? WHERE id=?", (latest, initial["id"]))
    result = client.get(f"/api/v1/evolution/populations/{latest}")
    assert result.status_code == 200 and result.json()["current"] is False
