"""History change list (ADR 0181): bounded word-level comparison of exact saved versions."""

from copy import deepcopy

import pytest
from test_change_undo import edit, offered, ok, step, structure
from test_change_undo import row as row

from cbs_transcriber import change_list
from cbs_transcriber.transcripts import normalize_segments


def changes(client, current, version_id=None, **params):
    version_id = version_id or current["active_version"]["id"]
    return client.get(
        f"/api/recordings/{current['id']}/transcripts/{current['active_transcript_id']}"
        f"/versions/{version_id}/changes",
        params=params,
    )


def listed(client, current):
    response = client.get(
        f"/api/recordings/{current['id']}/transcripts/{current['active_transcript_id']}/versions"
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_text_edit_shows_word_level_insertions_and_deletions(client, row):
    current = edit(client, row, 1, "Beta three changed four 🌸")
    result = ok(changes(client, current))
    assert result["base"]["sequence"] == 1 and result["sequence"] == 2
    assert result["counts"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 2}
    [item] = result["passages"]
    assert item["kind"] == "changed" and item["position"] == 2
    assert item["changes"][0] == "text"
    assert item["tokens"] == [
        {"op": "equal", "text": "Beta three"},
        {"op": "insert", "text": "changed"},
        {"op": "equal", "text": "four"},
        {"op": "insert", "text": "🌸"},
    ]
    assert item["speaker"] == "Anna"
    assert result["truncated"] is False and result["speaker_names"] == []


def test_long_equal_runs_collapse_and_huge_passages_are_rewritten(client, row, monkeypatch):
    words = [f"w{i}" for i in range(40)]
    current = edit(client, row, 0, " ".join(words))
    changed = words[:20] + ["NEW"] + words[20:]
    current = edit(client, current, 0, " ".join(changed))
    [item] = ok(changes(client, current))["passages"]
    assert item["tokens"] == [
        {"op": "equal", "text": "w0 w1 w2"},
        {"op": "skip", "count": 14},
        {"op": "equal", "text": "w17 w18 w19"},
        {"op": "insert", "text": "NEW"},
        {"op": "equal", "text": "w20 w21 w22"},
        {"op": "skip", "count": 14},
        {"op": "equal", "text": "w37 w38 w39"},
    ]
    monkeypatch.setattr(change_list, "MAX_WORDS", 10)
    [item] = ok(changes(client, current))["passages"]
    assert item["rewritten"] is True and "tokens" not in item


def test_structure_speaker_and_timing_changes_are_classified(client, row):
    store = client.app.state.store
    ids = [p["id"] for p in row["segments"]]
    merged = structure(client, row, ids[0], "merge", direction="down")
    result = ok(changes(client, merged))
    kinds = [(p["kind"], p["segment_id"]) for p in result["passages"]]
    assert kinds == [("changed", ids[0]), ("removed", ids[1])]
    assert result["passages"][1]["tokens"] == [{"op": "delete", "text": "Beta three four"}]
    renamed = store.save_transcript(
        row["id"],
        merged["segments"],
        {"A": "Anna Renamed"},
        merged["revision"],
        action="speaker_rename",
        result_id=row["active_transcript_id"],
    )
    result = ok(changes(client, renamed))
    assert result["passages"] == []
    assert result["speaker_names"] == [{"label": "A", "before": "Anna", "after": "Anna Renamed"}]
    segments = deepcopy(renamed["segments"])
    segments[1]["speaker"] = "B"
    segments[0]["start"] = 0.25
    moved = store.save_transcript(
        row["id"],
        segments,
        renamed["speakers"],
        renamed["revision"],
        action="manual_save",
        result_id=row["active_transcript_id"],
    )
    result = ok(changes(client, moved))
    by_id = {p["segment_id"]: p for p in result["passages"]}
    assert by_id[ids[0]]["changes"] == ["timing"]
    assert by_id[ids[2]]["changes"] == ["speaker"]
    assert by_id[ids[2]]["speaker_before"] == "Anna Renamed"
    assert by_id[ids[2]]["speaker"] == "Speaker_02"  # Default name derived from raw label B.


def test_creation_and_explicit_comparisons_and_bounds(client, row, monkeypatch):
    first = row["active_version"]["id"]
    created = ok(changes(client, row))
    assert created["base"] is None
    assert created["counts"]["added"] == 3
    assert [p["kind"] for p in created["passages"]] == ["added"] * 3
    current = edit(client, row, 0, "Alpha edited")
    current = edit(client, current, 2, "Gamma edited")
    whole = ok(changes(client, current, against=first))
    assert whole["base"]["version_id"] == first
    assert whole["counts"]["changed"] == 2
    store = client.app.state.store
    other = store.add_result(
        row["id"],
        normalize_segments([{"text": "Other method", "speaker": "B"}]),
        {},
        "Other",
        {},
    )
    other_version = store.result_versions(row["id"], other["id"])[0]["id"]
    across = ok(changes(client, current, against=other_version))
    assert across["base"]["result_id"] == other["id"]
    wrong = client.get(
        f"/api/recordings/{row['id']}/transcripts/{other['id']}/versions/{first}/changes"
    )
    assert wrong.status_code == 404
    monkeypatch.setattr(change_list, "MAX_LISTED", 1)
    assert ok(changes(client, current, against=first))["truncated"] is True
    monkeypatch.setattr(change_list, "MAX_PAYLOAD_BYTES", 10)
    large = ok(changes(client, current))
    assert large["too_large"] is True and "download both versions" in large["message"]


def test_damaged_versions_are_refused(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET payload=replace(payload,'Beta','Bet4') WHERE id=?",
            (row["active_version"]["id"],),
        )
    response = changes(client, current)
    assert response.status_code == 400
    assert "integrity" in response.json()["detail"]


def test_history_list_carries_bounded_change_metadata_only(client, row):
    store = client.app.state.store
    current = edit(client, row, 0, "Alpha edited")
    current = ok(step(client, current))
    current = ok(step(client, current, "redo"))
    rows = listed(client, current)
    by_action = {r["action"]: r for r in rows}
    edit_row = by_action["free_rewrite"]
    assert edit_row["change"] == {"segment_id": row["segments"][0]["id"]}
    assert by_action["change_undo"]["change"] == {
        "undone_version_id": edit_row["id"],
        "restored_from_version_id": row["active_version"]["id"],
    }
    assert by_action["change_redo"]["change"]["redone_version_id"] == edit_row["id"]
    assert by_action["provider_result"]["change"] == {}
    assert all("context" not in r and "reason" not in r for r in rows)
    with store.connection() as db:
        db.execute(
            "UPDATE result_versions SET context=json_set(context,'$.segment_id',?) WHERE id=?",
            ("x" * 201, edit_row["id"]),
        )
    assert (
        "segment_id" not in {r["id"]: r for r in listed(client, current)}[edit_row["id"]]["change"]
    )
    assert change_list.list_context(
        "result_merge",
        {"merge": {"summary": {"updated_passages": 2, "conflicts": 1}, "base": {}}},
    ) == {"summary": {"conflicts": 1, "updated_passages": 2}}
    assert change_list.list_context("result_merge", {"merge": {"summary": {"x": -1}}}) == {}


def test_options_list_the_exact_revertible_merge_and_split_versions(client, row):
    ids = [p["id"] for p in row["segments"]]
    merged = structure(client, row, ids[0], "merge", direction="down")
    current = edit(client, merged, 1, "Gamma later edit")
    split = structure(client, current, ids[2], "split-v2", cursor=6)
    available = offered(client, split)
    assert sorted(
        (item["kind"], item["segment_id"], item["version_id"]) for item in available["revertible"]
    ) == sorted(
        [
            ("merge", ids[0], merged["active_version"]["id"]),
            ("split", ids[2], split["active_version"]["id"]),
        ]
    )


@pytest.mark.parametrize("bad", ["x" * 101])
def test_against_is_bounded(client, row, bad):
    assert changes(client, row, against=bad).status_code == 422
