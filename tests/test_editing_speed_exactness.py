"""Long-transcript editing speed work keeps exact results (ADR 0193). Synthetic data only.

Each optimisation is compared with the earlier computation on the same library state:
verified passage slices against fresh canonical encoding, the undo/review scans against
their plain encode path, and shared snapshot encoding against plain indented JSON.
"""

import json
import shutil
from pathlib import Path

import pytest

from cbs_transcriber import change_undo, history, passage_review, passage_undo
from cbs_transcriber import store as store_module
from cbs_transcriber.store import Store, indented_json
from cbs_transcriber.transcripts import (
    delete_passage,
    merge_passages,
    normalize_segments,
    split_passage_manual,
)

CANONICAL = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}
TRICKY = [
    'Quote " and backslash \\ and },{"id":"x"} and ,"speakers":{}',
    "Line\nbreak, tab\t, U+2028   and NUL-free control \u0001",
    "Æøå café é emoji 🧪 and 中文",
    "]",
]


def _rows(count):
    rows = []
    for index in range(count):
        tokens = [f"ord{(index * 7 + offset) % 31}" for offset in range(6)]
        if index < len(TRICKY):
            tokens[0] = TRICKY[index].replace(" ", "_")
        words = [
            {
                "word": token,
                "start": index * 5 + offset * 0.1,
                "end": index * 5 + offset * 0.1 + 0.05,
                "probability": [0.1, 1e-7, 0.999999, 1.0][offset % 4],
            }
            for offset, token in enumerate(tokens)
        ]
        rows.append(
            {
                "text": " ".join(tokens),
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "speaker": f"S{index % 3}",
                "words": words,
                "avg_logprob": -0.25,
            }
        )
    return rows


def _seed(root, count=40):
    store = Store(root / "library")
    project = store.create_project("Synthetic exactness", storage_parent=root / "projects")
    recording = store.create_recording(project["id"], "synthetic.wav")
    speakers = {"S0": "Åse", "S1": 'Quote " voice', "S2": ',"speakers":'}
    store.add_result(
        recording["id"],
        normalize_segments(_rows(count)),
        speakers,
        "Synthetic",
        {"kind": "provider", "capability": "words_speakers"},
        auto_first=True,
    )
    return store, store.recording(recording["id"])


def _save(store, row, segments, action, context):
    return store.save_transcript(
        row["id"],
        segments,
        row["speakers"],
        row["revision"],
        result_id=row["active_transcript_id"],
        action=action,
        context=context,
    )


def _history(store, row):
    """Merges, a manual split, deletes and their undos: the shapes the scans must prove."""
    for index in (3, 9):
        segment, adjacent = row["segments"][index], row["segments"][index + 1]
        row = _save(
            store,
            row,
            merge_passages(row["segments"], segment["id"], "down", None, row["speakers"])
            if segment["speaker"] == adjacent["speaker"]
            else merge_passages(
                row["segments"], segment["id"], "down", segment["speaker"], row["speakers"]
            ),
            "passage_merge",
            {
                "segment_id": segment["id"],
                "adjacent_segment_id": adjacent["id"],
                "direction": "down",
            },
        )
    target = row["segments"][15]
    cursor = len(target["text"].split(" ")[0]) + 1
    row = _save(
        store,
        row,
        split_passage_manual(row["segments"], target["id"], cursor),
        "passage_split",
        {
            "segment_id": target["id"],
            "cursor": cursor,
            "split_policy": "manual-word-boundary-v2",
        },
    )
    for index in (20, 25):
        row = _save(
            store,
            row,
            delete_passage(row["segments"], row["segments"][index]["id"]),
            "passage_delete",
            {"segment_id": row["segments"][index]["id"]},
        )
    edited = [dict(passage) for passage in row["segments"]]
    edited[1]["text"] = edited[1]["text"] + " rettet"
    return _save(store, row, edited, "manual_save", {}), (row["segments"][9]["id"], target["id"])


def _plain_reads(monkeypatch):
    """Force the earlier computation: decode only, so every comparison encodes again."""

    def plain(payload, digest):
        history.verify_payload(payload, digest)
        return json.loads(payload), None

    monkeypatch.setattr(passage_undo, "verified_passage_texts", plain)
    monkeypatch.setattr(passage_review, "verified_passage_texts", plain)
    # Every comparison is the original whole-value encoding comparison.
    monkeypatch.setattr(
        passage_undo.Canonical, "same", lambda self, left, right: passage_undo._same(left, right)
    )


PAYLOADS = [
    ([], {}),
    *(
        ([{"id": "a", "text": t, "n": [0.1, 1e-7, -0.0, 1e300, 2**70, True, None]}] * 2, {})
        for t in TRICKY
    ),
    (["plain string", 3, [1, {"b": {}}], {"id": "z", "nested": {"y": [], "x": {}}}], {"k": "v"}),
    ([{"id": "s", "text": "x"}], {"speakers": ',"speakers":{"a":1}}', "a": "]"}),
]


@pytest.mark.parametrize("segments,speakers", PAYLOADS)
def test_verified_slices_are_the_exact_canonical_encoding_of_fresh_objects(segments, speakers):
    payload, digest = history.encode_payload(segments, speakers)
    decoded, texts = history.verified_passage_texts(payload, digest)
    assert decoded == json.loads(payload)
    assert json.dumps(decoded, **CANONICAL) == json.dumps(json.loads(payload), **CANONICAL)
    assert list(decoded) == list(json.loads(payload))
    assert texts == [json.dumps(item, **CANONICAL) for item in decoded["segments"]]
    again, _ = history.verified_passage_texts(payload, digest)
    assert again == decoded and again["segments"] is not decoded["segments"]


@pytest.mark.parametrize("refreshed_hash", [False, True])
def test_verified_slices_still_refuse_changed_bytes(refreshed_hash):
    payload, digest = history.encode_payload([{"id": "a", "text": "Synthetic"}], {})
    history.verified_passage_texts(payload, digest)
    changed = payload + " "  # Not the canonical encoding, whatever hash is supplied.
    with pytest.raises(ValueError, match="integrity check"):
        history.verified_passage_texts(
            changed, history.text_hash(changed) if refreshed_hash else digest
        )


def test_canonical_comparison_matches_same_for_registered_and_new_values():
    segments = [{"id": "a", "v": 1}, {"id": "b", "v": 1.0}, {"id": "c", "v": True}]
    payload, digest = history.encode_payload(segments, {})
    registry = passage_undo.Canonical()
    decoded = registry.read(payload, digest)["segments"]
    candidates = [
        decoded,
        list(decoded),
        decoded[:2],
        [*decoded[:2], {"id": "c", "v": 1}],
        [*decoded[:2], {"v": True, "id": "c"}],
        [*decoded[:2], ({"id": "c", "v": True},)[0]],
        [(1, 2)],
        [[1, 2]],
        [],
    ]
    for left in candidates:
        for right in candidates:
            assert registry.same(left, right) is passage_undo._same(left, right)
    for left in [*decoded, {"id": "a", "v": 1.0}, {"id": "a", "v": 1}]:
        for right in decoded:
            assert registry.same(left, right) is passage_undo._same(left, right)


def test_undo_options_and_review_reads_equal_the_plain_computation(tmp_path, monkeypatch):
    store, row = _seed(tmp_path)
    row, _ = _history(store, row)
    for steps in (False, True):
        fast = passage_undo.options(store, row["id"], row["active_transcript_id"], steps=steps)
        with monkeypatch.context() as patch:
            _plain_reads(patch)
            plain = passage_undo.options(store, row["id"], row["active_transcript_id"], steps=steps)
        assert fast == plain
    assert fast["segment_ids"] and fast["split_segment_ids"] and fast["deleted_segments"]
    assert fast["undo"]["available"] is True
    with passage_review._CONTEXT_LOCK:
        passage_review._CONTEXTS.clear()
    fast = passage_review.read(store, row["id"], row["active_transcript_id"])
    with passage_review._CONTEXT_LOCK:
        passage_review._CONTEXTS.clear()
    with monkeypatch.context() as patch:
        _plain_reads(patch)
        plain = passage_review.read(store, row["id"], row["active_transcript_id"])
    assert fast == plain
    head = store.result_versions(row["id"], row["active_transcript_id"])[0]
    payload = store.result_version(row["id"], row["active_transcript_id"], head["id"])["payload"]
    expected = {p["id"]: passage_review.fingerprint(p) for p in payload["segments"]}
    assert {p["segment_id"]: p["passage_sha256"] for p in fast["passages"]} == expected


def _versions(store):
    return [
        (v["sequence"], v["action"], v["payload"], v["payload_sha256"], v["reason"])
        for v in store.all("SELECT * FROM result_versions ORDER BY result_id,sequence")
    ]


def _context_without_ids(store):
    return [
        {k: v for k, v in json.loads(v["context"]).items() if not k.endswith("version_id")}
        for v in store.all("SELECT context FROM result_versions ORDER BY result_id,sequence")
    ]


@pytest.mark.parametrize("operation", ["merge", "split", "restore", "change_undo"])
def test_undo_applies_save_byte_identical_versions_to_the_plain_path(
    tmp_path, monkeypatch, operation
):
    store, row = _seed(tmp_path / "fast")
    row, (merged_id, split_id) = _history(store, row)
    copy = tmp_path / "plain"
    shutil.copytree(tmp_path / "fast", copy)
    plain_store = Store(copy / "library")
    options = passage_undo.options(store, row["id"], row["active_transcript_id"], steps=True)
    deleted = options["deleted_segments"][0]

    def run(target):
        if operation == "merge":
            return passage_undo.apply(
                target, row["id"], row["active_transcript_id"], merged_id, row["revision"]
            )
        if operation == "split":
            return passage_undo.apply_split(
                target, row["id"], row["active_transcript_id"], split_id, row["revision"]
            )
        if operation == "restore":
            return passage_undo.apply_restore(
                target,
                row["id"],
                row["active_transcript_id"],
                deleted["segment_id"],
                deleted["delete_version_id"],
                row["revision"],
            )
        # The newest step is a text save; the next is a structural delete (proof agreement).
        current = row
        for _ in range(2):
            offered = passage_undo.options(
                target, row["id"], row["active_transcript_id"], steps=True
            )
            current = change_undo.undo(
                target,
                row["id"],
                row["active_transcript_id"],
                current["revision"],
                offered["head_version_id"],
                offered["undo"]["version_id"],
            )
        return current

    fast = run(store)
    with monkeypatch.context() as patch:
        _plain_reads(patch)
        plain = run(plain_store)
    assert _versions(store) == _versions(plain_store)
    assert _context_without_ids(store) == _context_without_ids(plain_store)
    assert json.dumps(fast["segments"]) == json.dumps(plain["segments"])
    assert fast["revision"] == plain["revision"]


def test_structural_scans_encode_no_whole_transcript(tmp_path, monkeypatch):
    store, row = _seed(tmp_path, 120)
    row, _ = _history(store, row)
    whole = len(store.one("SELECT segments FROM recordings WHERE id=?", (row["id"],))["segments"])
    encoded = []
    original = json.dumps

    def counted(value, *args, **kwargs):
        text = original(value, *args, **kwargs)
        if len(text) > whole // 3:
            encoded.append(len(text))
        return text

    monkeypatch.setattr(passage_undo.json, "dumps", counted)
    passage_undo.options(store, row["id"], row["active_transcript_id"], steps=True)
    assert encoded == []
    with monkeypatch.context() as patch:
        _plain_reads(patch)
        passage_undo.options(store, row["id"], row["active_transcript_id"], steps=True)
    assert len(encoded) > 4  # The plain path encodes whole transcripts for each proof.


DOCUMENTS = [
    lambda shared: {"a": [{"recording": shared, "transcripts": [{"x": 1, "segments": shared}]}]},
    lambda shared: {"deep": {"er": {"still": [[shared], {"k": shared}]}}, "top": shared},
]
VALUES = [
    [],
    {},
    [{"text": t, "n": [1.5, None, True], "e": {}, "l": []} for t in TRICKY],
    ["cbs-snapshot-0000-0", "x"],
]


@pytest.mark.parametrize("make", DOCUMENTS)
@pytest.mark.parametrize("value", VALUES)
def test_shared_indented_encoding_is_byte_identical(make, value):
    document = make(value)
    places = []

    def find(node):
        if isinstance(node, dict):
            for key, item in node.items():
                if item is value:
                    places.append((node, key))
                find(item)
        elif isinstance(node, list):
            for item in node:
                find(item)

    find(document)
    before = json.dumps(document)
    assert indented_json(document, places) == json.dumps(document, ensure_ascii=False, indent=2)
    assert json.dumps(document) == before  # Temporary placeholders are always removed.


def test_shared_encoding_falls_back_when_a_placeholder_also_occurs_in_content(monkeypatch):
    monkeypatch.setattr(store_module.secrets, "token_hex", lambda size: "fixed")
    value = [{"text": "x"}]
    document = {"a": value, "b": value, "c": "cbs-snapshot-fixed-0"}
    expected = json.dumps(document, ensure_ascii=False, indent=2)
    assert indented_json(document, [(document, "a"), (document, "b")]) == expected


def _snapshot_bytes(store, row):
    folder = Path(store.project_folder(row["project_id"]))
    exports = sorted((folder / "exports").rglob("*.txt"))
    return (folder / "project.json").read_bytes(), [path.read_bytes() for path in exports]


def test_saved_recording_snapshot_and_response_match_the_plain_path(tmp_path, monkeypatch):
    store, row = _seed(tmp_path, 60)
    other = store.create_recording(row["project_id"], "second.wav")
    store.add_result(
        other["id"], normalize_segments(_rows(5)), {}, "Other", {"kind": "manual"}, auto_first=True
    )
    store.add_result(row["id"], normalize_segments(_rows(3)), {}, "Inactive", {"kind": "manual"})
    edited = [dict(passage) for passage in row["segments"]]
    edited[4]["text"] = "Rettet " + edited[4]["text"]
    loads = []
    original = json.loads
    raw = None

    def counted(value, *args, **kwargs):
        if value == raw:
            loads.append(True)
        return original(value, *args, **kwargs)

    saved = _save(store, row, edited, "manual_save", {})
    raw = store.one("SELECT segments FROM recordings WHERE id=?", (row["id"],))["segments"]
    fast = _snapshot_bytes(store, row)
    monkeypatch.setattr(store_module.json, "loads", counted)
    again = store.saved_recording(row["id"])
    assert len(loads) == 1  # Decoded once for the response; the snapshot reuses it.
    monkeypatch.setattr(store_module.json, "loads", original)
    assert _snapshot_bytes(store, row) == fast
    monkeypatch.setattr(
        store_module,
        "indented_json",
        lambda document, shared=(): json.dumps(document, ensure_ascii=False, indent=2),
    )
    store.snapshot_project(row["project_id"])
    assert _snapshot_bytes(store, row) == fast
    fresh = store.recording(row["id"])
    for response in (saved, again):
        assert json.dumps({**response, "snapshot_error": None}) == json.dumps(
            {**fresh, "snapshot_error": None}
        )


def test_snapshot_reuse_requires_exactly_equal_raw_mirrors(tmp_path):
    store, row = _seed(tmp_path, 8)
    raw = store.one("SELECT segments,speakers FROM recordings WHERE id=?", (row["id"],))
    stale = [{"id": "stale", "text": "never written"}]
    store.snapshot_project(
        row["project_id"],
        decoded=(row["id"], {"segments": (raw["segments"] + " ", stale)}),
    )
    snapshot = json.loads(
        (Path(store.project_folder(row["project_id"])) / "project.json").read_text("utf-8")
    )
    assert snapshot["recordings"][0]["segments"] == row["segments"]
    assert "never written" not in json.dumps(snapshot)
