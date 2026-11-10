"""Explicit runtime consent creates new immutable selections; it never edits failed requests."""

import copy
import threading

import pytest
from test_input_admission import inspect, item, native_provider
from test_input_admission import library as library

from cbs_transcriber import input_admission, providers, routing
from cbs_transcriber.features import FeatureRequest, effective_features
from cbs_transcriber.store import new_id, now
from cbs_transcriber.transcripts import normalize_segments


def blocked(store, provider, rows, wav_bytes, *, native=False):
    if native:
        choices = [
            {
                "recording_id": row["id"],
                "choice": input_admission.choice(
                    provider=provider,
                    requested_features={"speaker_labels": True},
                    enhancement={"execution": "ucloud"},
                    routing_snapshot={
                        "rules": routing.Rules().model_dump(),
                        "connection": provider,
                    },
                ),
            }
            for row in rows
        ]
    else:
        choices = [item(provider, row) for row in rows]
    accepted = input_admission.accept(store, choices)
    for row in rows:
        inspect(store, row, wav_bytes)
    while token := input_admission.claim(store):
        input_admission.materialize(
            store,
            token,
            {member["id"]: {"error": "needs_confirmation"} for member in token["jobs"]},
        )
    return [store.job(job["id"]) for job in accepted]


def cbs_provider(store, credentials):
    provider = native_provider(store, credentials)
    settings = providers.Settings.model_validate(provider["settings"]).model_copy(
        update={"model": "whisperx/large-v3"}
    )
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (settings.model_dump_json(), provider["id"])
    )
    return providers.profile(store, provider["id"])


def confirm(store, job, preview):
    intent = job["params"]["input_intent"]
    return input_admission.confirm_preparation(
        store,
        job["id"],
        epoch=intent["epoch"],
        phase=intent["phase"],
        fingerprint=preview["fingerprint"],
        confirmed=True,
    )


def test_v142_explicit_consent_clones_exact_choices_and_keeps_old_row_immutable(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = cbs_provider(store, credentials)
    with store.library_lock():
        old = blocked(store, provider, rows[:1], wav_bytes)[0]
        before = store.one("SELECT * FROM jobs WHERE id=?", (old["id"],))
        projection = input_admission.projection(store, old["id"])
        assert projection["can_prepare"] and projection["prepare_preview"]["count"] == 1
        result = confirm(store, old, projection["prepare_preview"])
        successor = result["jobs"][0]
        intent = successor["params"]["input_intent"]
        expected = copy.deepcopy(old["params"]["input_intent"]["accepted"])
        expected["prepare_runtime"] = expected["options"]["prepare_runtime"] = True
        # Only consent and its provenance change (ADR 0098).
        expected["prepare_consent"] = "request"
        assert intent["accepted"] == expected
        assert intent["predecessor"]["job_id"] == old["id"]
        assert (
            successor["id"] != old["id"]
            and intent["request_id"] != old["params"]["input_intent"]["request_id"]
        )
        assert store.one("SELECT * FROM jobs WHERE id=?", (old["id"],)) == before
        again = confirm(store, old, projection["prepare_preview"])
        assert [job["id"] for job in again["jobs"]] == [successor["id"]]
        continued = input_admission.projection(store, old["id"])
        assert continued["can_prepare"] is False and continued["prepare_preview"] is None
        assert continued["continuation"]["request_id"] == result["request_id"]


def stop_for_consent(store, jobs):
    """Background preflight stops the supplied (already inspected) intents for consent."""
    wanted = {job["id"] for job in jobs}
    while wanted:
        token = input_admission.claim(store)
        assert token is not None and {member["id"] for member in token["jobs"]} <= wanted
        input_admission.materialize(
            store,
            token,
            {member["id"]: {"error": "needs_confirmation"} for member in token["jobs"]},
        )
        wanted -= {member["id"] for member in token["jobs"]}
    return [store.job(job["id"]) for job in jobs]


def test_v0098_one_confirmation_continues_every_stopped_file_of_the_request(
    library, credentials, wav_bytes
):
    """B: a 12-file Røst request needed one Activity confirmation per file."""
    store, project, _, rows = library
    rows = [*rows, store.create_recording(project["id"], "third.wav")]
    provider = cbs_provider(store, credentials)
    with store.library_lock():
        old = blocked(store, provider, rows, wav_bytes)
        assert all(job["params"]["input_intent"]["native_group"] is None for job in old)
        before = [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old]
        previews = [input_admission.projection(store, job["id"])["prepare_preview"] for job in old]
        assert {preview["fingerprint"] for preview in previews} == {previews[0]["fingerprint"]}
        assert previews[0]["scope"] == "request_selection" and previews[0]["count"] == 3
        result = confirm(store, old[1], previews[1])
        assert {job["recording_id"] for job in result["jobs"]} == {row["id"] for row in rows}
        assert len({job["params"]["input_intent"]["request_id"] for job in result["jobs"]}) == 1
        predecessors = {
            job["params"]["input_intent"]["predecessor"]["job_id"] for job in result["jobs"]
        }
        assert predecessors == {job["id"] for job in old}
        # Any card of the confirmed scope returns the same successors; nothing is duplicated.
        again = confirm(store, old[2], previews[0])
        assert [job["id"] for job in again["jobs"]] == [job["id"] for job in result["jobs"]]
        assert [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old] == before
        for job in old:
            projection = input_admission.projection(store, job["id"])
            assert projection["can_prepare"] is False
            assert projection["continuation"]["request_id"] == result["request_id"]


def test_v0098_request_scope_skips_continued_siblings_and_other_choices(
    library, credentials, wav_bytes
):
    store, project, _, rows = library
    rows = [*rows, store.create_recording(project["id"], "third.wav")]
    provider = cbs_provider(store, credentials)
    turbo = providers.Settings.model_validate(provider["settings"]).model_copy(
        update={"model": "whisperx/large-v3-turbo"}
    )
    other_id = new_id()
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (other_id, "Synthetic turbo", turbo.model_dump_json(), now()),
    )
    other = providers.profile(store, other_id)
    with store.library_lock():
        accepted = input_admission.accept(
            store, [item(provider, row) for row in rows] + [item(other, rows[0])]
        )
        main, different = accepted[:3], accepted[3]
        assert len({job["params"]["input_intent"]["request_id"] for job in accepted}) == 1
        # The first file stops and is confirmed before its siblings have stopped.
        inspect(store, rows[0], wav_bytes)
        first, different = stop_for_consent(store, [main[0], different])
        early = input_admission.projection(store, first["id"])["prepare_preview"]
        assert early["count"] == 1 and early["scope"] == "recording"
        continued = confirm(store, first, early)["jobs"]
        assert [job["recording_id"] for job in continued] == [rows[0]["id"]]
        for row in rows[1:]:
            inspect(store, row, wav_bytes)
        later = stop_for_consent(store, main[1:])
        preview = input_admission.projection(store, later[0]["id"])["prepare_preview"]
        assert preview["count"] == 2
        result = confirm(store, later[0], preview)
        assert {job["recording_id"] for job in result["jobs"]} == {row["id"] for row in rows[1:]}
        assert (
            len(
                store.all(
                    "SELECT id FROM jobs "
                    "WHERE json_extract(params,'$.input_intent.predecessor.job_id')=?",
                    (first["id"],),
                )
            )
            == 1
        )
        # The sibling with a different accepted model keeps its own confirmation.
        own = input_admission.projection(store, different["id"])
        assert own["can_prepare"] and own["prepare_preview"]["count"] == 1
        assert own["continuation"] is None
        assert own["prepare_preview"]["recordings"][0]["provider_name"] == "Synthetic turbo"


def test_v142_native_consent_cohort_concurrent_confirm_creates_one_new_barrier(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    barrier, results = threading.Barrier(3), []
    with store.library_lock():
        old = blocked(store, provider, rows, wav_bytes, native=True)
        before = [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old]
        preview = input_admission.projection(store, old[0]["id"])["prepare_preview"]
        assert preview["scope"] == "native_selection" and preview["count"] == 2

        def submit(job):
            barrier.wait()
            results.append(confirm(store, job, preview))

        threads = [threading.Thread(target=submit, args=(job,)) for job in old]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(10)
            assert not thread.is_alive()
        assert len(results) == 2
        assert results[0]["request_id"] == results[1]["request_id"]
        identifiers = {job["id"] for job in results[0]["jobs"]}
        assert identifiers == {job["id"] for job in results[1]["jobs"]}
        assert len(identifiers) == 2
        assert all(
            set(job["params"]["input_intent"]["native_group"]["job_ids"]) == identifiers
            for job in results[0]["jobs"]
        )
        assert [store.one("SELECT * FROM jobs WHERE id=?", (job["id"],)) for job in old] == before
        assert not store.all("SELECT id FROM native_batch_runs")


def test_v142_consent_requires_exact_fingerprint_confirmation_and_current_policy(
    library, credentials, wav_bytes
):
    store, project, _, rows = library
    provider = cbs_provider(store, credentials)
    with store.library_lock():
        old = blocked(store, provider, rows[:1], wav_bytes)[0]
        preview = input_admission.projection(store, old["id"])["prepare_preview"]
        count = len(store.all("SELECT id FROM jobs"))
        changed = {**preview, "fingerprint": "0" * 64}
        with pytest.raises(input_admission.StaleInput):
            confirm(store, old, changed)
        intent = old["params"]["input_intent"]
        with pytest.raises(ValueError, match="Confirm"):
            input_admission.confirm_preparation(
                store,
                old["id"],
                epoch=intent["epoch"],
                phase="terminal",
                fingerprint=preview["fingerprint"],
                confirmed=False,
            )
        store.execute("UPDATE projects SET classification='protected' WHERE id=?", (project["id"],))
        with pytest.raises(ValueError, match="policy"):
            confirm(store, old, preview)
        assert len(store.all("SELECT id FROM jobs")) == count


def test_v142_consent_keeps_accepted_profile_when_visibility_disabled(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = cbs_provider(store, credentials)
    with store.library_lock():
        old = blocked(store, provider, rows[:1], wav_bytes)[0]
        preview = input_admission.projection(store, old["id"])["prepare_preview"]
        changed = providers.Settings.model_validate(provider["settings"]).model_copy(
            update={"enabled": False}
        )
        store.execute(
            "UPDATE providers SET value=? WHERE id=?", (changed.model_dump_json(), provider["id"])
        )
        result = confirm(store, old, preview)
    assert (
        result["jobs"][0]["params"]["input_intent"]["accepted"]["provider"]["settings"]
        == provider["settings"]
    )
    with pytest.raises(RuntimeError, match="lease"):
        confirm(store, old, preview)


def test_v142_native_prepare_clones_only_failed_consent_members_not_materialized_sibling(
    library, credentials, wav_bytes
):
    store, project, _, rows = library
    rows = [*rows, store.create_recording(project["id"], "third.wav")]
    provider = native_provider(store, credentials)
    with store.library_lock():
        choices = [
            {
                "recording_id": row["id"],
                "choice": input_admission.choice(
                    provider=provider,
                    requested_features={"speaker_labels": True},
                    enhancement={"execution": "ucloud"},
                    routing_snapshot={
                        "rules": routing.Rules().model_dump(),
                        "connection": provider,
                    },
                ),
            }
            for row in rows[:2]
        ] + [item(provider, rows[2])]
        accepted = input_admission.accept(store, choices)
        for row in rows:
            inspect(store, row, wav_bytes)
        token = input_admission.claim(store)
        source = store.job(accepted[2]["id"])["params"]["input_intent"]
        params = {
            "settings": provider["settings"],
            "provider_id": provider["id"],
            "provider_name": provider["name"],
            "revision": source["baseline"]["revision"],
            "audio_input": token["receipts"][accepted[2]["id"]],
            "audio_transport": "original",
            "requested_features": effective_features(
                providers.Settings.model_validate(provider["settings"]), FeatureRequest()
            ).model_dump(),
        }
        results = input_admission.materialize(
            store,
            token,
            {
                accepted[0]["id"]: {"error": "needs_confirmation"},
                accepted[1]["id"]: {"error": "needs_confirmation"},
                accepted[2]["id"]: {"params": params},
            },
        )
        old = results[0]
        materialized = store.one("SELECT * FROM jobs WHERE id=?", (accepted[2]["id"],))
        preview = input_admission.projection(store, old["id"])["prepare_preview"]
        assert preview["count"] == 2
        renewed = confirm(store, old, preview)
        assert {job["recording_id"] for job in renewed["jobs"]} == {row["id"] for row in rows[:2]}
        assert store.one("SELECT * FROM jobs WHERE id=?", (accepted[2]["id"],)) == materialized
        assert all(
            len(job["params"]["input_intent"]["native_group"]["job_ids"]) == 2
            for job in renewed["jobs"]
        )


def test_v142_whole_consent_keeps_historical_source_after_edit_and_trash(
    library, credentials, wav_bytes
):
    store, _, _, rows = library
    provider = native_provider(store, credentials)
    original = normalize_segments(
        [{"start": 0, "end": 3, "text": "Synthetic originally approved wording."}]
    )
    saved = store.add_result(rows[0]["id"], original, {}, "Reviewed", {})
    current = store.recording(rows[0]["id"])
    with store.library_lock():
        accepted = input_admission.accept(
            store,
            [
                {
                    "recording_id": rows[0]["id"],
                    "choice": input_admission.choice(
                        options={"execution": "ucloud"},
                        source={"result_id": saved["id"], "revision": current["revision"]},
                        routing_snapshot={
                            "rules": routing.Rules().model_dump(),
                            "connection": provider,
                        },
                    ),
                }
            ],
        )[0]
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        old = input_admission.materialize(
            store, token, {accepted["id"]: {"error": "needs_confirmation"}}
        )[0]
        preview = input_admission.projection(store, old["id"])["prepare_preview"]
        snapshot = copy.deepcopy(old["params"]["input_intent"]["source_snapshot"])
        store.save_transcript(
            rows[0]["id"],
            normalize_segments([{"start": 0, "end": 3, "text": "Synthetic newer wording."}]),
            {},
            current["revision"],
            result_id=saved["id"],
        )
        current = store.recording(rows[0]["id"])
        store.set_result_trashed(rows[0]["id"], saved["id"], current["revision"], trashed=True)
        renewed = confirm(store, old, preview)["jobs"][0]
    assert renewed["params"]["input_intent"]["source_snapshot"] == snapshot
    assert renewed["params"]["input_intent"]["source_snapshot"]["segments"] == original
    assert not store.results(rows[0]["id"])


def test_v142_consent_rechecks_the_accepted_followup_connection_not_only_parent(
    library, credentials, wav_bytes
):
    store, project, _, rows = library
    parent = native_provider(store, credentials)
    other_id = new_id()
    value = providers.Settings.model_validate(parent["settings"]).model_copy(
        update={"approved_for": ["protected"]}
    )
    store.execute(
        "UPDATE providers SET value=? WHERE id=?", (value.model_dump_json(), parent["id"])
    )
    parent = providers.profile(store, parent["id"])
    store.execute(
        "INSERT INTO providers VALUES (?,?,?,?)",
        (other_id, "Synthetic follow-up", value.model_dump_json(), now()),
    )
    followup = providers.profile(store, other_id)
    with store.library_lock():
        accepted = input_admission.accept(
            store,
            [
                {
                    "recording_id": rows[0]["id"],
                    "choice": input_admission.choice(
                        provider=parent,
                        requested_features={"speaker_labels": True},
                        enhancement={"execution": "ucloud"},
                        routing_snapshot={
                            "rules": routing.Rules().model_dump(),
                            "connection": followup,
                        },
                    ),
                }
            ],
        )[0]
        inspect(store, rows[0], wav_bytes)
        token = input_admission.claim(store)
        old = input_admission.materialize(
            store, token, {accepted["id"]: {"error": "needs_confirmation"}}
        )[0]
        preview = input_admission.projection(store, old["id"])["prepare_preview"]
        count = len(store.all("SELECT id FROM jobs"))
        store.execute("UPDATE projects SET classification='protected' WHERE id=?", (project["id"],))
        store.execute(
            "UPDATE providers SET value=? WHERE id=?",
            (value.model_copy(update={"approved_for": []}).model_dump_json(), other_id),
        )
        with pytest.raises(ValueError, match="policy"):
            confirm(store, old, preview)
        assert len(store.all("SELECT id FROM jobs")) == count
