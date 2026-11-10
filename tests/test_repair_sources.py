"""Frozen source proofs use synthetic SQLite snapshots, never audio or provider runtimes."""

import copy
import hashlib
import json
from contextlib import contextmanager

import pytest

from cbs_transcriber import repair_sources
from cbs_transcriber.history import encode_payload
from cbs_transcriber.store import Store
from cbs_transcriber.transcripts import normalize_segments


@pytest.fixture
def study(tmp_path):
    return seed(Store(tmp_path / "library"))


def seed(store):
    project = store.create_project("PRIVATE_STUDY")
    recording = store.create_recording(project["id"], "PRIVATE_RECORDING.wav")
    store.execute("UPDATE recordings SET status='ready',duration=20 WHERE id=?", (recording["id"],))
    result = store.add_result(
        recording["id"],
        [
            normalize_segments([row])[0] | {"id": str(index)}
            for index, row in enumerate(
                [
                    {"text": "PRIVATE_FIRST æøå.", "start": 0, "end": 2},
                    {"text": "God morgen.", "start": 2, "end": 5},
                    {"text": "Tak fordi", "start": None, "end": None},
                    {"text": "du kom.", "start": None, "end": None},
                    {"text": "Farvel igen.", "start": 9, "end": 12},
                ]
            )
        ],
        {"UNKNOWN": "PRIVATE_SPEAKER"},
        "PRIVATE_RESULT",
        {"kind": "import"},
    )
    return store, project, recording, result


def options(**changes):
    return {"align": True, "diarize": False, "transcribe": False, "retranscribe": False} | changes


def frozen_job(study, kind="alignment", ids=None):
    store, _, recording, result = study
    ids = ids if ids is not None else (["2", "3"] if kind == "group" else ["1"])
    params = {"enhancement": options()}
    params.update({"segment_id": ids[0]} if len(ids) == 1 else {"segment_ids": ids})
    if kind in ("group", "selected"):
        source = store.freeze_result(
            recording["id"], result["id"], store.recording(recording["id"])["revision"]
        )
        params |= {
            "source_snapshot": source
            | {"segments": [s for s in source["segments"] if s["id"] in ids]},
            "passage_timing": True,
            "realign": True,
            "timing_units": [{"ids": ids, "start": 5, "end": 9}]
            if kind == "group"
            else [{"ids": [i], "start": 2, "end": 5} for i in ids],
        }
        return store.add_job(recording["id"], "enhance", params)
    if kind == "retranscription":
        params["enhancement"]["retranscribe"] = True
        params["retranscription"] = True
    if kind == "autoedit":
        params["realign"] = True
    return store.enqueue_enhancement(
        recording["id"], result["id"], store.recording(recording["id"])["revision"], params
    )


def save_params(store, job, params):
    store.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps(params), job["id"]))


@pytest.mark.parametrize("kind", ["alignment", "retranscription", "autoedit", "selected", "group"])
def test_real_frozen_shapes_bind_full_version_and_separate_projection(study, kind):
    store, _, recording, result = study
    job = frozen_job(study, kind)
    source = job["params"]["source_snapshot"]
    ids = job["params"].get("segment_ids", [job["params"].get("segment_id")])
    full = store.result_version(recording["id"], result["id"], source["source_version_id"])
    selected = [s for s in full["payload"]["segments"] if s["id"] in ids]
    receipt = repair_sources.inspect_repair_source(store, job["id"])
    assert receipt == {
        "format": repair_sources.FORMAT,
        "operation": "retranscription"
        if kind == "retranscription"
        else "alignment"
        if kind == "alignment"
        else "timing",
        "job_id": job["id"],
        "recording_id": recording["id"],
        "result_id": result["id"],
        "source_version_id": source["source_version_id"],
        "source_payload_sha256": source["source_payload_sha256"],
        "projection_sha256": encode_payload(selected, source["speakers"])[1],
        "observed_params_sha256": hashlib.sha256(
            store.one("SELECT params FROM jobs WHERE id=?", (job["id"],))["params"].encode()
        ).hexdigest(),
        "source_scope": "selected" if kind in ("selected", "group") else "full",
        "segment_ids": ids,
    }
    assert receipt["projection_sha256"] != receipt["source_payload_sha256"]
    serialized = json.dumps(receipt)
    for private in (
        "PRIVATE_FIRST",
        "PRIVATE_SPEAKER",
        "PRIVATE_RESULT",
        "PRIVATE_RECORDING",
        str(store.root),
        "start",
        "end",
        "text",
        "speakers",
    ):
        assert private not in serialized


def test_scope_is_operation_derived_when_selected_equals_full(study):
    store, _, _, result = study
    # Every member is selected; the gap is one group, not two independent untimed units.
    job = frozen_job(study, "selected", [s["id"] for s in result["segments"]])
    params = job["params"]
    params["timing_units"] = [
        {"ids": ["0"], "start": 0, "end": 2},
        {"ids": ["1"], "start": 2, "end": 5},
        {"ids": ["2", "3"], "start": 5, "end": 9},
        {"ids": ["4"], "start": 9, "end": 12},
    ]
    save_params(store, job, params)
    receipt = repair_sources.inspect_repair_source(store, job["id"])
    assert receipt["source_scope"] == "selected"
    assert receipt["projection_sha256"] == receipt["source_payload_sha256"]


def test_passage_ids_remain_literal_unicode_data_in_an_internal_receipt(study):
    store, _, recording, result = study
    segments = copy.deepcopy(result["segments"])
    literal = "Spørgsmål / æøå. 1"
    segments[1]["id"] = literal
    changed = store.add_result(recording["id"], segments, result["speakers"], "Unicode", {})
    job = frozen_job((store, None, recording, changed), ids=[literal])
    receipt = repair_sources.inspect_repair_source(store, job["id"])
    assert receipt["segment_ids"] == [literal]
    assert "PRIVATE_SPEAKER" not in json.dumps(receipt)


@pytest.mark.parametrize(
    "damage", ["text", "original", "speakers", "order", "extra", "missing", "wrong_scope"]
)
def test_frozen_projection_tampering_is_refused(study, damage):
    store, _, _, _ = study
    job = frozen_job(study, "group")
    params = copy.deepcopy(job["params"])
    source = params["source_snapshot"]
    if damage == "text":
        source["segments"][0]["text"] = "PRIVATE_TAMPER"
    elif damage == "original":
        source["segments"][0]["original_text"] = "PRIVATE_TAMPER"
    elif damage == "speakers":
        source["speakers"]["UNKNOWN"] = "PRIVATE_TAMPER"
    elif damage == "order":
        source["segments"].reverse()
    elif damage == "extra":
        source["segments"].append(copy.deepcopy(source["segments"][0]))
    elif damage == "missing":
        source["segments"].pop()
    else:
        params.pop("passage_timing")
        params.pop("timing_units")
    save_params(store, job, params)
    with pytest.raises(ValueError, match="projection"):
        repair_sources.inspect_repair_source(store, job["id"])


@pytest.mark.parametrize("swap", ["recording", "result", "version", "digest"])
def test_same_payload_hash_cannot_replace_recording_result_version_ownership(study, swap):
    store, project, recording, result = study
    job = frozen_job(study)
    other_recording = store.create_recording(project["id"], "other.wav")
    other = store.add_result(
        other_recording["id"], result["segments"], result["speakers"], "Other", {}
    )
    other_source = store.freeze_result(
        other_recording["id"], other["id"], store.recording(other_recording["id"])["revision"]
    )
    assert (
        other_source["source_payload_sha256"]
        == job["params"]["source_snapshot"]["source_payload_sha256"]
    )
    params = job["params"]
    if swap == "recording":
        store.execute(
            "UPDATE jobs SET recording_id=? WHERE id=?", (other_recording["id"], job["id"])
        )
    elif swap == "result":
        params["source_snapshot"]["id"] = other["id"]
    elif swap == "version":
        params["source_snapshot"]["source_version_id"] = other_source["source_version_id"]
    else:
        params["source_snapshot"]["source_payload_sha256"] = "0" * 64
    save_params(store, job, params)
    with pytest.raises(ValueError, match="ownership|hash"):
        repair_sources.inspect_repair_source(store, job["id"])
    assert store.recording(recording["id"])["active_transcript_id"] == result["id"]


@pytest.mark.parametrize("damage", ["bytes", "rehashed", "duplicate_ids"])
def test_damaged_or_ambiguously_rehashed_preserved_payload_cannot_be_certified(study, damage):
    store, _, _, _ = study
    job = frozen_job(study)
    params = job["params"]
    source = params["source_snapshot"]
    if damage == "bytes":
        payload, digest = "{}", source["source_payload_sha256"]
    else:
        segments = copy.deepcopy(source["segments"])
        if damage == "rehashed":
            segments[0]["text"] = "PRIVATE_TAMPER"
        else:
            segments[0]["id"] = segments[1]["id"]
            source["segments"] = segments
        payload, digest = encode_payload(segments, source["speakers"])
        if damage == "duplicate_ids":
            source["source_payload_sha256"] = digest
            save_params(store, job, params)
    store.execute(
        "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
        (payload, digest, source["source_version_id"]),
    )
    with pytest.raises(ValueError):
        repair_sources.inspect_repair_source(store, job["id"])


@pytest.mark.parametrize(
    "damage",
    [
        "both",
        "null_single",
        "null_many",
        "empty",
        "duplicate",
        "unknown",
        "bool_flag",
        "missing_flag",
        "missing_version",
        "null_version",
    ],
)
def test_malformed_selectors_flags_or_legacy_missing_proof_refuse_only_inspection(study, damage):
    store, _, _, _ = study
    job = frozen_job(study)
    params = job["params"]
    if damage == "both":
        params["segment_ids"] = ["1"]
    elif damage == "null_single":
        params["segment_id"] = None
    elif damage in ("null_many", "empty", "duplicate", "unknown"):
        params.pop("segment_id")
        params["segment_ids"] = {
            "null_many": None,
            "empty": [],
            "duplicate": ["1", "1"],
            "unknown": ["unknown"],
        }[damage]
    elif damage == "bool_flag":
        params["enhancement"]["align"] = 1
    elif damage == "missing_flag":
        params["enhancement"].pop("transcribe")
    elif damage == "missing_version":
        params["source_snapshot"].pop("source_version_id")
    else:
        params["source_snapshot"]["source_version_id"] = None
    save_params(store, job, params)
    before = store.one("SELECT params,state FROM jobs WHERE id=?", (job["id"],))
    with pytest.raises(ValueError):
        repair_sources.inspect_repair_source(store, job["id"])
    assert store.one("SELECT params,state FROM jobs WHERE id=?", (job["id"],)) == before


@pytest.mark.parametrize("kind", ["alignment", "autoedit", "retranscription"])
def test_full_snapshot_selectors_may_retain_requested_order_but_receipts_use_source_order(
    study, kind
):
    store, _, _, _ = study
    job = frozen_job(study, kind, ["4", "1"])
    assert job["params"]["segment_ids"] == ["4", "1"]
    assert repair_sources.inspect_repair_source(store, job["id"])["segment_ids"] == ["1", "4"]


@pytest.mark.parametrize("change", ["numeric_type", "signed_zero", "bool_as_int"])
def test_projection_uses_exact_canonical_bytes_even_when_python_values_compare_equal(study, change):
    store, _, _, _ = study
    job = frozen_job(study)
    params = copy.deepcopy(job["params"])
    passage = params["source_snapshot"]["segments"][0]
    if change == "numeric_type":
        passage["start"] = int(passage["start"])
    elif change == "signed_zero":
        passage["start"] = -0.0
    else:
        passage["word_timing"] = 0
    assert params == job["params"]
    assert json.dumps(params) != json.dumps(job["params"])
    save_params(store, job, params)
    with pytest.raises(ValueError, match="projection"):
        repair_sources.inspect_repair_source(store, job["id"])


@pytest.mark.parametrize(
    "damage",
    ["split", "overlap", "reversed", "cross_bounded", "missing", "nan", "bool", "too_long"],
)
def test_timing_groups_prove_membership_without_certifying_audio_windows(study, damage):
    store, _, _, _ = study
    job = frozen_job(study, "group")
    params = job["params"]
    unit = params["timing_units"][0]
    if damage == "split":
        params["timing_units"] = [{"ids": [i], "start": 5, "end": 9} for i in ("2", "3")]
    elif damage == "overlap":
        params["timing_units"].append(copy.deepcopy(unit))
    elif damage == "reversed":
        unit["ids"].reverse()
    elif damage == "cross_bounded":
        unit["ids"] = ["1", "2", "3"]
    elif damage == "missing":
        unit["ids"].pop()
    elif damage == "nan":
        unit["start"] = float("nan")
    elif damage == "bool":
        unit["start"] = False
    else:
        unit["end"] = 100
    save_params(store, job, params)
    with pytest.raises(ValueError):
        repair_sources.inspect_repair_source(store, job["id"])


def test_historical_receipt_does_not_depend_on_today_head_active_trash_or_duration(study):
    store, _, recording, result = study
    job = frozen_job(study, "group")
    first = repair_sources.inspect_repair_source(store, job["id"])
    current = store.recording(recording["id"])
    changed = copy.deepcopy(current["segments"])
    changed[1]["text"] = "New reviewed head"
    store.save_transcript(
        recording["id"], changed, current["speakers"], current["revision"], action="free_rewrite"
    )
    store.set_result_trashed(
        recording["id"], result["id"], store.recording(recording["id"])["revision"], trashed=True
    )
    store.execute("UPDATE recordings SET duration=1 WHERE id=?", (recording["id"],))
    assert repair_sources.inspect_repair_source(store, job["id"]) == first


def test_one_read_snapshot_retains_coherent_job_and_version_during_writer_interleaving(
    study, monkeypatch
):
    store, _, _, _ = study
    job = frozen_job(study)
    expected = repair_sources.inspect_repair_source(store, job["id"])
    original = store.connection
    writer = Store(store.root)
    changed = False

    class Interleaved:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, args=()):
            nonlocal changed
            cursor = self.db.execute(sql, args)
            if sql.startswith("SELECT recording_id,kind,length") and not changed:
                changed = True
                params = copy.deepcopy(job["params"])
                params["source_snapshot"]["segments"][0]["text"] = "PRIVATE_TAMPER"
                save_params(writer, job, params)
            return cursor

    @contextmanager
    def connection():
        with original() as db:
            yield Interleaved(db)

    monkeypatch.setattr(store, "connection", connection)
    assert repair_sources.inspect_repair_source(store, job["id"]) == expected
    assert changed
    with pytest.raises(ValueError, match="projection"):
        repair_sources.inspect_repair_source(store, job["id"])


def test_inspection_uses_one_read_connection_without_helpers_or_current_projection(
    study, monkeypatch
):
    store, _, _, _ = study
    job = frozen_job(study)
    original = store.connection
    statements, connections = [], []

    class ReadOnly:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, args=()):
            assert sql == "BEGIN" or sql.startswith("SELECT ")
            assert "active_transcript_id" not in sql and "FROM recordings" not in sql
            statements.append(sql)
            return self.db.execute(sql, args)

    @contextmanager
    def connection():
        connections.append(True)
        with original() as db:
            yield ReadOnly(db)

    def refuse(*args, **kwargs):
        raise AssertionError("The source inspector must not read today's projections or files.")

    monkeypatch.setattr(store, "connection", connection)
    for helper in (
        "job",
        "recording",
        "result",
        "freeze_result",
        "all",
        "one",
        "execute",
        "directory",
    ):
        monkeypatch.setattr(store, helper, refuse)
    assert repair_sources.inspect_repair_source(store, job["id"])["job_id"] == job["id"]
    assert connections == [True] and statements[0] == "BEGIN"


def test_observed_params_hash_changes_with_mutable_output_without_changing_source_proof(study):
    store, _, _, _ = study
    job = frozen_job(study)
    before = repair_sources.inspect_repair_source(store, job["id"])
    params = copy.deepcopy(job["params"])
    params["realign_output"] = {"passages": {}}
    save_params(store, job, params)
    after = repair_sources.inspect_repair_source(store, job["id"])
    assert after.pop("observed_params_sha256") != before.pop("observed_params_sha256")
    assert after == before


@pytest.mark.parametrize("budget", ["params", "payload", "total"])
def test_byte_budgets_precede_fetch_and_parse_without_large_fixtures(study, monkeypatch, budget):
    store, _, _, _ = study
    job = frozen_job(study, "selected")
    original = store.connection
    reads = []
    params_size = len(
        store.one("SELECT params FROM jobs WHERE id=?", (job["id"],))["params"].encode()
    )
    payload_size = len(
        store.one(
            "SELECT payload FROM result_versions WHERE id=?",
            (job["params"]["source_snapshot"]["source_version_id"],),
        )["payload"].encode()
    )
    assert payload_size > params_size
    limit = 1 if budget == "params" else params_size if budget == "payload" else payload_size
    monkeypatch.setattr(repair_sources, "MAX_RECORD_BYTES", limit)
    if budget == "total":
        monkeypatch.setattr(repair_sources, "MAX_TOTAL_BYTES", params_size + payload_size - 1)

    class Counted:
        def __init__(self, db):
            self.db = db

        def execute(self, sql, args=()):
            if sql.startswith("SELECT params FROM") or sql.startswith("SELECT payload FROM"):
                reads.append(sql)
            return self.db.execute(sql, args)

    @contextmanager
    def connection():
        with original() as db:
            yield Counted(db)

    monkeypatch.setattr(store, "connection", connection)
    with pytest.raises(ValueError, match="byte limit"):
        repair_sources.inspect_repair_source(store, job["id"])
    assert len(reads) == (0 if budget == "params" else 1)


@pytest.mark.parametrize(
    "raw", ['{"segment_id":"1","segment_id":"2"}', '{"x":NaN}', '{"x":1e999}', "[]", "{"]
)
def test_malformed_params_fail_without_revealing_private_content(study, raw):
    store, _, _, _ = study
    job = frozen_job(study)
    store.execute("UPDATE jobs SET params=? WHERE id=?", (raw, job["id"]))
    with pytest.raises(ValueError) as exc:
        repair_sources.inspect_repair_source(store, job["id"])
    assert raw not in str(exc.value)


@pytest.mark.parametrize("kind", ["timing", "alignment", "retranscription"])
def test_current_api_frozen_shapes_are_inspected_without_runtime_or_audio_work(
    client, monkeypatch, kind
):
    from cbs_transcriber import enhancement

    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    store, _, recording, result = seed(client.app.state.store)
    body = {
        "result_id": result["id"],
        "revision": store.recording(recording["id"])["revision"],
        "segment_ids": ["2"] if kind == "timing" else ["4", "1"],
    }
    path = {
        "timing": "passage-timing",
        "alignment": "enhancements",
        "retranscription": "retranscriptions",
    }[kind]
    if kind != "timing":
        body |= {"execution": "local", "align": True, "diarize": False, "allow_downloads": False}
    response = client.post(f"/api/recordings/{recording['id']}/{path}", json=body)
    assert response.status_code == 202, response.text
    job = store.job(response.json()["id"])
    assert ("segment_id" in job["params"]) != ("segment_ids" in job["params"])
    receipt = repair_sources.inspect_repair_source(store, job["id"])
    assert receipt["source_scope"] == ("selected" if kind == "timing" else "full")
    assert receipt["segment_ids"] == (["2", "3"] if kind == "timing" else ["1", "4"])
    assert store.job(job["id"])["state"] == "queued"


@pytest.mark.parametrize("marker", [True, 1, "yes", None, {}, []])
def test_full_transcription_dispatch_marker_never_gets_a_scoped_source_receipt(study, marker):
    store, _, _, _ = study
    job = frozen_job(study)
    params = job["params"]
    params["transcription"] = marker
    save_params(store, job, params)
    with pytest.raises(ValueError):
        repair_sources.inspect_repair_source(store, job["id"])


def test_literal_false_full_transcription_marker_remains_compatible(study):
    store, _, _, _ = study
    job = frozen_job(study)
    params = job["params"] | {"transcription": False}
    save_params(store, job, params)
    assert repair_sources.inspect_repair_source(store, job["id"])["operation"] == "alignment"


@pytest.mark.parametrize("location", ["unit", "source"])
def test_huge_numeric_bounds_refuse_with_curated_value_error(study, location):
    store, _, _, _ = study
    job = frozen_job(study, "group")
    params = job["params"]
    huge = 10**400
    if location == "unit":
        params["timing_units"][0].update(start=huge, end=huge + 1)
    else:
        source = params["source_snapshot"]
        version = store.result_version(
            job["recording_id"], source["id"], source["source_version_id"]
        )
        version["payload"]["segments"][0]["start"] = huge
        payload, digest = encode_payload(
            version["payload"]["segments"], version["payload"]["speakers"]
        )
        source["source_payload_sha256"] = digest
        store.execute(
            "UPDATE result_versions SET payload=?,payload_sha256=? WHERE id=?",
            (payload, digest, source["source_version_id"]),
        )
    save_params(store, job, params)
    with pytest.raises(ValueError):
        repair_sources.inspect_repair_source(store, job["id"])
