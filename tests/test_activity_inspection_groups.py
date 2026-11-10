"""Inspection cards group only through unique, owned, project-scoped upload receipts."""

import uuid

from test_activity_remove import finished, hide, upload
from test_restored_activity import mark

from cbs_transcriber import original_inspection, restored_owners


def create_inspections(client, store, project_id, wav_bytes, batch, count=2):
    rows = [
        upload(client, project_id, wav_bytes, f"file-{index}.wav", upload_batch=batch).json()
        for index in range(count)
    ]
    caches = [original_inspection.ensure(store, row["recording"]["id"]) for row in rows]
    return rows, caches


def cancel(store, cache):
    original_inspection.cancel(
        store, cache["inspection_job_id"], generation=cache["generation"], epoch=cache["epoch"]
    )


def test_current_upload_inspections_hide_together_after_every_member_finishes(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Inspection upload grouping")
    batch = uuid.uuid4().hex
    with store.library_lock():
        rows, caches = create_inspections(client, store, project["id"], wav_bytes, batch)
        cancel(store, caches[0])
        assert hide(client, caches[0]["inspection_job_id"]).status_code == 409
        assert client.get("/api/activity").json()["hidden"] == {}
        cancel(store, caches[1])
        listed = {job["id"]: job for job in client.get("/api/jobs").json()}
        for cache in caches:
            item = listed[cache["inspection_job_id"]]
            assert item["inspection_upload_batch"] == batch
            assert item["input_preparation"]["generation"] == cache["generation"]
            assert item["project_id"] == project["id"]
        before = store.all("SELECT * FROM jobs ORDER BY id")
        response = hide(client, caches[0]["inspection_job_id"])
        assert response.status_code == 200, response.text
        assert response.json()["hidden"] == {
            cache["inspection_job_id"]: listed[cache["inspection_job_id"]]["updated"]
            for cache in caches
        }
        assert store.all("SELECT * FROM jobs ORDER BY id") == before
        assert all(row["job"]["id"] not in response.json()["hidden"] for row in rows)


def test_same_upload_marker_in_another_project_is_a_separate_inspection_group(client, wav_bytes):
    store = client.app.state.store
    projects = [store.create_project(f"Project {i}") for i in range(2)]
    batch = uuid.uuid4().hex
    with store.library_lock():
        groups = [
            create_inspections(client, store, project["id"], wav_bytes, batch)[1]
            for project in projects
        ]
        for cache in groups[0]:
            cancel(store, cache)
        # Pending inspectors in another project do not retain this finished group.
        response = hide(client, groups[0][0]["inspection_job_id"])
        assert response.status_code == 200, response.text
        assert set(response.json()["hidden"]) == {c["inspection_job_id"] for c in groups[0]}


def test_missing_ambiguous_and_invalid_inspections_do_not_join_upload_group(client, wav_bytes):
    store = client.app.state.store
    project = store.create_project("Inspection receipt guards")
    batch = uuid.uuid4().hex
    with store.library_lock():
        rows, caches = create_inspections(client, store, project["id"], wav_bytes, batch, 4)
        stale = finished(store, rows[0]["recording"]["id"], kind="source-inspect")
        # A second media job makes the durable association ambiguous, even if its marker agrees.
        finished(store, rows[1]["recording"]["id"], kind="media", params={"upload_batch": batch})
        store.execute(
            "UPDATE recordings SET original_input='{}' WHERE id=?", (rows[2]["recording"]["id"],)
        )
        with store.connection() as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='restored_owners'"
            ).fetchone():
                db.execute(restored_owners.DDL)
        mark(store, "job", rows[3]["job"]["id"])
        legacy = store.create_recording(project["id"], "legacy.wav")
        legacy_cache = original_inspection.ensure(store, legacy["id"])
        for cache in [*caches[:2], caches[3], legacy_cache]:
            cancel(store, cache)
        listed = {job["id"]: job for job in client.get("/api/jobs").json()}
        assert listed[caches[0]["inspection_job_id"]]["inspection_upload_batch"] == batch
        assert listed[caches[1]["inspection_job_id"]]["inspection_upload_batch"] is None
        assert listed[legacy_cache["inspection_job_id"]]["inspection_upload_batch"] is None
        assert listed[caches[3]["inspection_job_id"]]["inspection_upload_batch"] is None
        assert (
            listed[caches[2]["inspection_job_id"]]["input_preparation"]["error_code"]
            == "invalid_evidence"
        )
        assert "inspection_upload_batch" not in listed[caches[2]["inspection_job_id"]]
        assert listed[stale]["input_preparation"]["error_code"] == "invalid_evidence"
        assert "inspection_upload_batch" not in listed[stale]
        response = hide(client, caches[0]["inspection_job_id"])
        assert response.status_code == 200, response.text
        assert set(response.json()["hidden"]) == {caches[0]["inspection_job_id"]}
        assert hide(client, caches[2]["inspection_job_id"]).status_code == 409
