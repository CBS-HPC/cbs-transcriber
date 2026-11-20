"""Branch view graph (ADR 0182): bounded, text-free metadata of results and versions."""

import json

from test_change_undo import edit, ok, step
from test_change_undo import row as row
from test_result_merges import apply, preview
from test_result_merges import branch as branch

from cbs_transcriber import version_graph


def graph(client, recording_id):
    response = client.get(f"/api/recordings/{recording_id}/version-graph")
    assert response.status_code == 200, response.text
    return response.json()


def test_lanes_branch_point_and_merge_edge_without_text(client, branch):
    planned = preview(client, branch)
    applied = apply(client, branch, planned)
    assert applied.status_code == 200, applied.text
    rid = branch["recording"]["id"]
    result = graph(client, rid)
    lanes = {lane["id"]: lane for lane in result["results"]}
    ours, incoming = lanes[branch["ours"]["id"]], lanes[branch["incoming"]["id"]]
    assert [v["action"] for v in ours["versions"]] == [
        "provider_result",
        "free_rewrite",
        "result_merge",
    ]
    assert ours["branch"] is None and ours["source"] == {
        "kind": "provider",
        "model": "synthetic-asr",
    }
    assert incoming["branch"] == {
        "result_id": ours["id"],
        "version_id": ours["versions"][0]["id"],
    }
    assert result["edges"] == [
        {
            "kind": "merge",
            "from": {"result_id": incoming["id"], "version_id": incoming["versions"][0]["id"]},
            "to": {"result_id": ours["id"], "version_id": ours["versions"][-1]["id"]},
        }
    ]
    assert result["active_result_id"] == ours["id"]
    assert result["limited"] is False
    text = json.dumps(result, ensure_ascii=False)
    for private in ("København", "Review this", "Named researcher", "private-source-path"):
        assert private not in text


def test_undo_redo_links_working_trash_and_bounds(client, row, monkeypatch):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    current = ok(step(client, current))
    current = ok(step(client, current, "redo"))
    other = store.add_result(row["id"], current["segments"], current["speakers"], "Other", {})
    current = store.recording(row["id"])
    store.set_result_trashed(row["id"], other["id"], current["revision"], trashed=True)
    result = graph(client, row["id"])
    lane = next(lane for lane in result["results"] if lane["id"] == row["active_transcript_id"])
    edit_id = lane["versions"][1]["id"]
    assert lane["versions"][2]["undone_version_id"] == edit_id
    assert lane["versions"][3]["redone_version_id"] == edit_id
    assert result["working"] == {
        "result_id": row["active_transcript_id"],
        "version_id": lane["versions"][3]["id"],
    }
    trashed = next(lane for lane in result["results"] if lane["id"] == other["id"])
    assert trashed["trashed"] is True and trashed["branch"] is None
    monkeypatch.setattr(version_graph, "MAX_VERSIONS", 2)
    limited = graph(client, row["id"])
    lane = next(r for r in limited["results"] if r["id"] == row["active_transcript_id"])
    assert [v["sequence"] for v in lane["versions"]] == [3, 4]
    assert lane["truncated"] is True and lane["total_versions"] == 4
    monkeypatch.setattr(version_graph, "MAX_RESULTS", 1)
    assert graph(client, row["id"])["limited"] is True


def test_unknown_or_foreign_sources_are_shown_as_gaps(client, row):
    store = client.app.state.store
    derived = store.add_result(row["id"], row["segments"], row["speakers"], "Derived", {})
    first = store.result_versions(row["id"], derived["id"])[-1]["id"]
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET "
            "context=json_set(context,'$.source_version_unknown',json('true')) WHERE id=?",
            (first,),
        )
    lanes = {lane["id"]: lane for lane in graph(client, row["id"])["results"]}
    assert lanes[derived["id"]]["branch"] == "unknown"
    assert client.get("/api/recordings/missing/version-graph").status_code == 404
