"""Explicit wording adoption is snapshot-bound, timing-invalidating and atomic."""

import json
import sqlite3
from contextlib import contextmanager
from copy import deepcopy

import pytest
from test_result_merges import rows

from cbs_transcriber import result_actions, wording_adoptions
from cbs_transcriber.history import text_hash
from cbs_transcriber.transcripts import edit_segment, normalize_segments, word_mapping_current


@pytest.fixture
def adoption(client):
    store = client.app.state.store
    project = store.create_project("Synthetic adoption")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segments = normalize_segments(
        [
            {
                "text": "Old wording.",
                "start": 1,
                "end": 4,
                "speaker": "A",
                "words": [
                    {"word": "Old", "start": 1, "end": 2},
                    {"word": "wording.", "start": 2, "end": 4},
                ],
            },
            {"text": "Keep this literal æ\u0301.", "start": None, "end": None, "speaker": "B"},
        ]
    )
    segments[0]["words_current"] = True
    segments[0]["unknown"] = {"literal": ["keep", "æ\u0301"]}
    parent = store.add_result(
        recording["id"], segments, {"A": "Named voice"}, "Original", {"kind": "import"}
    )
    current = store.recording(recording["id"])
    frozen = store.freeze_result(recording["id"], parent["id"], current["revision"])
    job = store.add_job(recording["id"], "enhance", {"source_snapshot": frozen})
    target = store.add_result(
        recording["id"],
        deepcopy(segments),
        {"A": "Named voice"},
        "Reviewed",
        {"kind": "enhancement", "source_result_id": parent["id"], "model": "synthetic"},
        job_id=job["id"],
    )
    current = store.activate_result(recording["id"], target["id"], current["revision"])
    source_segments = normalize_segments(
        [
            {
                "text": "  Ny 👩‍🔬 æ\u0301 — wording.\n",
                "start": 100,
                "end": 200,
                "speaker": "FOREIGN",
            },
            {"text": "Another alternative.", "start": None, "end": None},
        ]
    )
    source_segments[0]["id"] = "source-æ-1"
    source = store.add_result(
        recording["id"],
        source_segments,
        {"FOREIGN": "Other voice"},
        "Alternative",
        {"kind": "import"},
    )
    store.save_notes("recordings", recording["id"], "Independent notes", 0)
    return {
        "store": store,
        "recording": current,
        "target": target,
        "source": source,
        "frozen": frozen,
        "url": f"/api/recordings/{recording['id']}/transcripts/{target['id']}",
        "request": {
            "source_result_id": source["id"],
            "target_passage_id": "0",
            "source_passage_id": "source-æ-1",
            "revision": current["revision"],
        },
    }


def preview(client, adoption, **changes):
    return client.post(
        adoption["url"] + "/wording-preview", json={**adoption["request"], **changes}
    )


def adopt(client, adoption, planned, **changes):
    return client.post(
        adoption["url"] + "/wording-adopt",
        json={**adoption["request"], "fingerprint": planned["fingerprint"], **changes},
    )


def rewrite_source(adoption, *, text=None, remove=False, duplicate=False):
    store, source = adoption["store"], adoption["source"]
    segments = deepcopy(source["segments"])
    if remove:
        segments.pop(0)
    elif duplicate:
        segments.append(deepcopy(segments[0]))
    elif text is not None:
        segments[0] = edit_segment(segments[0], "free", text)
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "UPDATE transcript_results SET segments=? WHERE id=?",
            (json.dumps(segments, ensure_ascii=False), source["id"]),
        )
        return store._append_version(
            db,
            db.execute("SELECT * FROM transcript_results WHERE id=?", (source["id"],)).fetchone(),
            "manual_save",
            {},
            "local_user",
        )


def test_preview_is_one_snapshot_and_returns_exact_pair_with_real_head(
    client, adoption, monkeypatch
):
    store = adoption["store"]
    before = rows(store)
    connect = store.connection
    calls, statements = [], []

    @contextmanager
    def traced():
        with connect() as db:
            calls.append(db)
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(store, "connection", traced)
    response = preview(client, adoption)
    assert response.status_code == 200, response.text
    assert len(calls) == 1 and "BEGIN" in statements
    assert not any(sql.startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)
    monkeypatch.setattr(store, "connection", connect)
    planned = response.json()
    assert planned["target_passage"] == {
        "id": "0",
        "text": "Old wording.",
        "start": 1,
        "end": 4,
        "ordinal": 1,
    }
    assert planned["source_passage"] == {
        "id": "source-æ-1",
        "text": adoption["source"]["segments"][0]["text"],
        "start": 100,
        "end": 200,
        "ordinal": 1,
    }
    source_head = store.result_versions(adoption["recording"]["id"], adoption["source"]["id"])[0]
    assert planned["source_version_id"] == source_head["id"] != source_head["observed_at"]
    assert rows(store) == before
    assert preview(client, adoption).json() == planned


def test_adoption_preserves_everything_except_selected_wording_and_current_timing(client, adoption):
    store, current, target, source = (
        adoption["store"],
        adoption["recording"],
        adoption["target"],
        adoption["source"],
    )
    before = rows(store)
    head_before = store.result_versions(current["id"], target["id"])[0]
    planned = preview(client, adoption).json()
    response = adopt(client, adoption, planned, reason="Literal <adoption> æøå")
    assert response.status_code == 200, response.text
    saved = response.json()
    recording = saved["recording"]
    passage = recording["segments"][0]
    expected = edit_segment(target["segments"][0], "free", source["segments"][0]["text"])
    assert passage == expected
    assert passage["text"] == source["segments"][0]["text"]
    assert passage["start"] == 1 and passage["end"] == 4 and passage["speaker"] == "A"
    assert passage["original_text"] == "Old wording."
    assert passage["words"] == target["segments"][0]["words"]
    assert not passage["word_timing"] and passage["edited"] and "words_current" not in passage
    assert not word_mapping_current(passage)
    assert recording["segments"][1] == target["segments"][1]
    assert recording["speakers"] == target["speakers"]
    assert recording["revision"] == current["revision"] + 1
    assert recording["notes"] == "Independent notes" and recording["notes_revision"] == 1
    assert store.result(current["id"], source["id"]) == source
    assert store.result(current["id"], target["id"])["source"] == target["source"]
    head = store.result_version(current["id"], target["id"], saved["version_id"])
    assert head["action"] == "wording_adopt" and head["actor_kind"] == "local_user"
    assert head["predecessor_id"] == head_before["id"]
    assert head["source_version_id"] == adoption["frozen"]["source_version_id"]
    assert head["reason"] == "Literal <adoption> æøå"
    assert head["context"]["processing"] == head_before["context"]["processing"]
    evidence = head["context"]["wording_adoption"]
    assert evidence == {
        "format": "cbs-wording-adoption-v1",
        "source": {
            "result_id": source["id"],
            "version_id": planned["source_version_id"],
            "payload_sha256": store.result_versions(current["id"], source["id"])[0][
                "payload_sha256"
            ],
        },
        "target_passage_id": "0",
        "source_passage_id": "source-æ-1",
        "target_text_sha256": text_hash("Old wording."),
        "source_text_sha256": text_hash(passage["text"]),
    }
    after = rows(store)
    for table in ("jobs", "projects", "note_versions", "result_trash"):
        assert after[table] == before[table]
    assert after["result_versions"][:-1] == before["result_versions"]
    assert adopt(client, adoption, planned).status_code == 409
    assert rows(store) == after
    assert preview(client, adoption, revision=recording["revision"]).status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        "target",
        "active",
        "source-head",
        "source-text",
        "source-blank",
        "source-equal",
        "source-delete",
        "source-trash",
        "target-trash",
    ],
)
def test_stale_inputs_refuse_without_a_write(client, adoption, change):
    planned = preview(client, adoption).json()
    store, current = adoption["store"], adoption["recording"]
    if change == "target":
        store.save_transcript(
            current["id"],
            adoption["target"]["segments"],
            adoption["target"]["speakers"],
            current["revision"],
        )
    elif change == "active":
        store.activate_result(current["id"], adoption["source"]["id"], current["revision"])
    elif change.endswith("trash"):
        store.set_result_trashed(
            current["id"],
            adoption["source" if change == "source-trash" else "target"]["id"],
            current["revision"],
            trashed=True,
        )
    else:
        text = {
            "source-text": "Newer source wording.",
            "source-blank": " \n",
            "source-equal": "Old wording.",
        }.get(change)
        rewrite_source(adoption, text=text, remove=change == "source-delete")
    before = rows(store)
    response = adopt(client, adoption, planned)
    assert response.status_code == 409, response.text
    assert "another window" in response.json()["detail"]
    assert rows(store) == before


@pytest.mark.parametrize("text", ["", " \t\n", "Old wording."])
def test_blank_or_identical_source_is_never_adopted(client, adoption, text):
    rewrite_source(adoption, text=text)
    before = rows(adoption["store"])
    assert preview(client, adoption).status_code == 400
    assert adopt(client, adoption, {"fingerprint": "0" * 64}).status_code in {400, 409}
    assert rows(adoption["store"]) == before


@pytest.mark.parametrize(
    "damage",
    [
        "target-hash",
        "source-hash",
        "target-mirror",
        "source-mirror",
        "target-duplicate",
        "source-duplicate",
    ],
)
def test_corrupt_mirrors_hashes_and_duplicate_ids_are_refused(client, adoption, damage):
    store, current = adoption["store"], adoption["recording"]
    chosen = adoption["target" if damage.startswith("target") else "source"]
    if damage.endswith("hash"):
        head = store.result_versions(current["id"], chosen["id"])[0]
        store.execute(
            "UPDATE result_versions SET payload_sha256=? WHERE id=?", ("0" * 64, head["id"])
        )
    elif damage == "source-duplicate":
        rewrite_source(adoption, duplicate=True)
    elif damage == "target-duplicate":
        segments = deepcopy(chosen["segments"])
        segments.append(deepcopy(segments[0]))
        store.save_transcript(current["id"], segments, chosen["speakers"], current["revision"])
        adoption["request"]["revision"] += 1
    elif damage == "target-mirror":
        store.execute("UPDATE recordings SET speakers='{}' WHERE id=?", (current["id"],))
    else:
        store.execute("UPDATE transcript_results SET speakers='{}' WHERE id=?", (chosen["id"],))
    before = rows(store)
    assert preview(client, adoption).status_code == 400
    assert rows(store) == before


def test_self_cross_recording_and_missing_passages_are_refused(client, adoption):
    store, current = adoption["store"], adoption["recording"]
    other_recording = store.create_recording(current["project_id"], "other.wav")
    other = store.add_result(
        other_recording["id"], normalize_segments([{"text": "Foreign."}]), {}, "Other", {}
    )
    before = rows(store)
    assert preview(client, adoption, source_result_id=adoption["target"]["id"]).status_code == 400
    assert preview(client, adoption, source_result_id=other["id"]).status_code == 400
    for field in ("target_passage_id", "source_passage_id", "source_result_id"):
        assert preview(client, adoption, **{field: "missing"}).status_code == 404
    assert rows(store) == before


def test_fingerprint_binds_both_selected_passage_ids(client, adoption):
    planned = preview(client, adoption).json()
    before = rows(adoption["store"])
    assert adopt(client, adoption, planned, target_passage_id="1").status_code == 409
    assert adopt(client, adoption, planned, source_passage_id="1").status_code == 409
    assert adopt(client, adoption, planned, fingerprint="0" * 64).status_code == 409
    assert rows(adoption["store"]) == before


def test_explicit_passage_choice_keeps_an_untimed_targets_bounds_and_correct_ordinals(
    client, adoption
):
    response = preview(client, adoption, target_passage_id="1", source_passage_id="1")
    assert response.status_code == 200, response.text
    planned = response.json()
    assert planned["target_passage"]["ordinal"] == planned["source_passage"]["ordinal"] == 2
    assert planned["target_passage"]["start"] is None and planned["target_passage"]["end"] is None
    # Choose a timed source explicitly: its bounds still cannot be assigned to the target.
    planned = preview(client, adoption, target_passage_id="1").json()
    response = adopt(client, adoption, planned, target_passage_id="1")
    assert response.status_code == 200, response.text
    passages = response.json()["recording"]["segments"]
    assert passages[0] == adoption["target"]["segments"][0]
    assert passages[1]["start"] is None and passages[1]["end"] is None
    assert passages[1]["speaker"] == "B"


def test_canonically_equivalent_unicode_is_adopted_without_normalization(client, adoption):
    store, current, target = adoption["store"], adoption["recording"], adoption["target"]
    segments = deepcopy(target["segments"])
    segments[0] = edit_segment(segments[0], "free", "é")
    saved = store.save_transcript(current["id"], segments, target["speakers"], current["revision"])
    adoption["request"]["revision"] = saved["revision"]
    rewrite_source(adoption, text="e\u0301")
    planned = preview(client, adoption)
    assert planned.status_code == 200, planned.text
    response = adopt(client, adoption, planned.json())
    assert response.status_code == 200, response.text
    assert response.json()["recording"]["segments"][0]["text"] == "e\u0301"


def test_oversize_source_passage_is_refused_without_a_write(client, adoption):
    store, current = adoption["store"], adoption["recording"]
    source = store.add_result(
        current["id"], normalize_segments([{"text": "x" * 200_001}]), {}, "Long", {}
    )
    before = rows(store)
    response = preview(client, adoption, source_result_id=source["id"], source_passage_id="0")
    assert response.status_code == 400
    assert rows(store) == before


def test_snapshot_observes_the_committed_adoption_once(client, adoption, monkeypatch):
    store = adoption["store"]
    planned = preview(client, adoption).json()
    observed = []

    def snapshot(project_id):
        current = store.recording(adoption["recording"]["id"])
        head = store.result_versions(current["id"], adoption["target"]["id"])[0]
        observed.append((project_id, current["revision"], head["action"]))

    monkeypatch.setattr(store, "snapshot_project", snapshot)
    assert adopt(client, adoption, planned).status_code == 200
    assert observed == [
        (
            adoption["recording"]["project_id"],
            adoption["recording"]["revision"] + 1,
            "wording_adopt",
        )
    ]


def test_append_failure_rolls_back_both_mirrors_and_never_snapshots(client, adoption, monkeypatch):
    store = adoption["store"]
    planned = preview(client, adoption).json()
    before = rows(store)
    store.execute(
        "CREATE TRIGGER reject_adoption BEFORE INSERT ON result_versions "
        "WHEN NEW.action='wording_adopt' BEGIN SELECT RAISE(ABORT,'synthetic rollback'); END"
    )
    snapshots = []
    monkeypatch.setattr(store, "snapshot_project", snapshots.append)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic rollback"):
        adopt(client, adoption, planned)
    assert rows(store) == before and not snapshots


@pytest.mark.parametrize(
    "extra",
    [
        {"text": "Injected"},
        {"segments": []},
        {"context": {}},
        {"revision": True},
        {"source_passage_id": "x" * 201},
        {"source_result_id": "x" * 101},
    ],
)
@pytest.mark.parametrize("route", ["wording-preview", "wording-adopt"])
def test_request_fields_are_strict_and_never_accept_client_wording(client, adoption, extra, route):
    body = {**adoption["request"], **extra}
    if route == "wording-adopt":
        body["fingerprint"] = "0" * 64
    before = rows(adoption["store"])
    assert client.post(adoption["url"] + "/" + route, json=body).status_code == 422
    assert rows(adoption["store"]) == before


@pytest.mark.parametrize("boundary", [result_actions, wording_adoptions])
def test_snapshot_limits_refuse_without_a_write(client, adoption, monkeypatch, boundary):
    monkeypatch.setattr(boundary, "MAX_TRANSCRIPT_BYTES", 100)
    before = rows(adoption["store"])
    assert preview(client, adoption).status_code == 400
    assert rows(adoption["store"]) == before
