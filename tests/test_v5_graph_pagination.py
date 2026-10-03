import base64
import json

import final_core
from tests.test_final import client
from tests.test_v5_graph import campaign, node


def test_graph_pages_retain_cross_page_edges_and_bound_both_streams(client):
    _, current = campaign(client)
    cid = current["id"]
    seed = node(client, cid, "claim", "Pagination seed")
    with final_core.connect() as db:
        template = dict(db.execute("SELECT * FROM research_nodes WHERE id=?", (seed["id"],)).fetchone())
        for index in range(1000):
            row = {**template, "id": f"page-node-{index:04d}"}
            if index == 999:
                row.update(node_type="canonical_result", source_ref="missing-claim")
            db.execute(f"INSERT INTO research_nodes ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        # More edge pages than node pages; includes reverse and cross-page edges.
        for index in range(1201):
            source, target = (seed["id"], f"page-node-{index:04d}") if index < 1000 else (f"page-node-{index-1000:04d}", "page-node-0999")
            db.execute("INSERT INTO research_edges VALUES(?,?,?,?,?,?,?)", (
                f"page-edge-{index:04d}", cid, source, target, "contradicts", "{}", template["created_at"],
            ))
    # The old induced-subgraph endpoint loses these relations across pages.
    legacy = client.get(f"/api/v1/research/campaigns/{cid}/graph?limit=200").json()
    assert len(legacy["edges"]) < 200
    url = f"/api/v1/research/campaigns/{cid}/graph/page"
    cursor, nodes, edges, pages, first_cursor = None, [], [], [], None
    for _ in range(10):
        response = client.get(url, params={"limit": 200, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["nodes"]) <= 200 and len(page["edges"]) <= 200
        nodes.extend(page["nodes"])
        edges.extend(page["edges"])
        pages.append(page)
        cursor = page["page"]["next_cursor"]
        first_cursor = first_cursor or cursor
        if not page["page"]["has_more"]:
            break
    assert cursor is None and len(pages) == 7
    assert pages[-1]["nodes"] == [] and len(pages[-1]["edges"]) == 1
    assert len(nodes) == len({item["id"] for item in nodes}) == 1001
    assert len(edges) == len({item["id"] for item in edges}) == 1201
    assert next(item for item in nodes if item["id"] == "page-node-0999")["canonical_trust"]["current"] is False
    assert {item["source_id"] for item in edges} <= {item["id"] for item in nodes}
    assert {item["target_id"] for item in edges} <= {item["id"] for item in nodes}
    _, other = campaign(client, "https://graph-pagination-other.example.test")
    assert client.get(f"/api/v1/research/campaigns/{other['id']}/graph/page", params={"cursor": first_cursor}).status_code == 422
    for value in ("bad-token", base64.urlsafe_b64encode(json.dumps([cid, 1, None, False, False]).encode()).decode()):
        assert client.get(url, params={"cursor": value}).status_code == 422
    empty = client.get(f"/api/v1/research/campaigns/{other['id']}/graph/page").json()
    assert empty["nodes"] == empty["edges"] == [] and empty["page"]["has_more"] is False
