"""One bounded composed workflow; all media, providers and cloud journals are synthetic.

This covers API/Store preservation, not browser playback, inference or remote acceptance.
The first case uses direct frozen legacy jobs. Its fresh-admission sibling uses real
source inspection/admission and portable capture/restore with a mocked API response.
"""

import gc
import hashlib
import io
import json
import sqlite3
import tempfile
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from cbs_transcriber import jobs, media, providers
from cbs_transcriber.app import create_app
from cbs_transcriber.store import SCHEMA_VERSION, new_id


@pytest.fixture
def short_preservation_root():
    # Generated staging/project/recording names need more space than pytest's parent allows.
    with tempfile.TemporaryDirectory(prefix="w-") as directory:
        yield Path(directory).resolve()
        gc.collect()  # Release unreachable SQLite/app cycles before owned Windows cleanup.


def digest(value):
    return hashlib.sha256(value).hexdigest()


def table_inventory(db):
    """Logical row hashes for this disposable fixture, not a portable backup codec."""
    tables = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    result = {}
    for (name,) in tables:
        rows = sorted(
            json.dumps(list(row), ensure_ascii=False, separators=(",", ":"))
            for row in db.execute(f'SELECT * FROM "{name}"')
        )
        result[name] = {"rows": len(rows), "sha256": digest("\n".join(rows).encode())}
    return result


def history_rows(store):
    """Copy every immutable field by ID before the operations under observation."""
    with store.connection() as db:
        db.execute("BEGIN")
        return {
            table: {row["id"]: dict(row) for row in db.execute(f"SELECT * FROM {table}")}
            for table in ("result_versions", "note_versions")
        }


def assert_history_preserved(store, baseline):
    observed = history_rows(store)
    for table, rows in baseline.items():
        assert {key: observed[table].get(key) for key in rows} == rows, (
            f"Preexisting {table} rows changed or disappeared"
        )


def test_two_projects_processing_review_export_backup_and_reopen(
    tmp_path, credentials, wav_bytes, monkeypatch
):
    calls, checkpoints = [], []
    source = {
        "segments": [
            {
                "text": "Hej København.",
                "start": 0,
                "end": 2,
                "speaker": "S0",
                "words": [
                    {"word": "Hej", "start": 0.1, "end": 0.7},
                    {"word": "København.", "start": 0.8, "end": 1.9},
                ],
            }
        ]
    }

    def prepare_media(directory, filename, cancel):
        # No decoder or playback artifact is claimed by this metadata-only mock.
        return {"kind": "audio", "duration": 3}

    def prepare_chunks(directory, identifier, duration, maximum, cancel):
        folder = directory / "chunks" / identifier
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "part.wav").write_bytes(wav_bytes)
        return folder, [{"file": "part.wav", "offset": 0}]

    def transcribe(path, settings, key):
        assert path.read_bytes() == wav_bytes and key == "synthetic-memory-only"
        calls.append({"stage": "provider", "model": settings.model})
        return deepcopy(source)

    def enhance(queue, job, recording):
        frozen = job["params"]["source_snapshot"]
        calls.append({"stage": "enhancement", "source_version_id": frozen["source_version_id"]})
        queue.store.add_result(
            recording["id"],
            frozen["segments"],
            {"S0": "Synthetic speaker"},
            "Mock enhancement",
            {"kind": "enhancement", "source_result_id": frozen["id"]},
            job_id=job["id"],
        )

    monkeypatch.setattr(media, "prepare_media", prepare_media)
    monkeypatch.setattr(media, "prepare_chunks", prepare_chunks)
    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    monkeypatch.setattr("cbs_transcriber.enhancement.run_enhancement", enhance)
    credentials.set(providers.OPENAI_URL, "synthetic-memory-only", False)
    root = tmp_path / "library"
    app = create_app(root, start_worker=False, credentials=credentials)
    store, queue = app.state.store, app.state.queue

    def checkpoint(stage, rid):
        row = store.recording(rid)
        checkpoints.append(
            {
                "stage": stage,
                "revision": row["revision"],
                "notes_revision": row["notes_revision"],
                "roles": row["result_roles"],
                "active_result_id": row["active_transcript_id"],
                "versions": store.one("SELECT COUNT(*) n FROM result_versions")["n"],
            }
        )

    def mark(client, rid, version_id):
        current = store.recording(rid)
        body = {
            "version_id": version_id,
            "revision": current["revision"],
            "role_revision": current["result_roles"]["revision"],
        }
        preview = client.post(f"/api/recordings/{rid}/working-preview", json=body)
        assert preview.status_code == 200, preview.text
        marked = client.post(
            f"/api/recordings/{rid}/working",
            json={**body, "fingerprint": preview.json()["fingerprint"]},
        )
        assert marked.status_code == 200, marked.text

    with TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        client.put("/api/editing-settings", json={"realign_edits": False}).raise_for_status()
        projects = [
            client.post("/api/projects", json={"name": name}).json() for name in ("A æøå", "B")
        ]
        collection = client.post(
            f"/api/projects/{projects[0]['id']}/collections", json={"name": "Wave 🧪"}
        ).json()
        recording_ids, originals = [], {}
        for index, project in enumerate((projects[0], projects[0], projects[1])):
            data = wav_bytes[:-2] + index.to_bytes(2, "little")
            uploaded = client.post(
                "/api/recordings",
                data={"project_id": project["id"]},
                files={"file": (f"synthetic-{index}.æøå.wav", data, "audio/wav")},
            )
            assert uploaded.status_code == 201, uploaded.text
            uploaded = uploaded.json()
            rid, job_id = uploaded["recording"]["id"], uploaded["job"]["id"]
            store.update_job(job_id, state="running")
            queue.process(job_id)
            store.update_job(job_id, state="completed", progress=1)
            recording_ids.append(rid)
            originals[rid] = digest(data)
        rid, missing, control = recording_ids
        client.post(
            f"/api/projects/{projects[0]['id']}/collections/move",
            json={"recording_ids": [rid], "collection_id": collection["id"]},
        ).raise_for_status()
        for target in (rid, control):
            imported = client.post(
                f"/api/recordings/{target}/transcript",
                data={"revision": 0},
                files={"file": ("synthetic.json", json.dumps(source).encode(), "application/json")},
            )
            assert imported.status_code == 200, imported.text

        # B has older transcript and notes versions as well as a current projection.
        client.patch(
            f"/api/recordings/{control}/segments/0",
            json={"revision": 1, "mode": "free", "text": "Project B current wording."},
        ).raise_for_status()
        for url in (
            f"/api/projects/{projects[1]['id']}/notes",
            f"/api/recordings/{control}/notes",
        ):
            for revision in range(2):
                client.patch(
                    url, json={"revision": revision, "text": f"Project B notes {revision + 1}."}
                ).raise_for_status()
        setup_history = history_rows(store)

        # Drive actual queue publication and its atomic frozen follow-up insertion.
        child_id = new_id()
        provider_jobs = []
        for index in range(2):
            params = {"settings": providers.Settings(capability="words").model_dump()}
            if index == 0:
                params["followup"] = {
                    "id": child_id,
                    "params": {"enhancement": {"execution": "local"}},
                }
            parent = store.add_job(rid, "transcribe", params)
            store.update_job(parent["id"], state="running")
            queue.process(parent["id"])
            assert store.job(parent["id"])["state"] == "completed"
            provider_jobs.append(parent["id"])
        first = store.one("SELECT id FROM transcript_results WHERE job_id=?", (provider_jobs[0],))
        first_version = store.result_versions(rid, first["id"])[0]
        assert (
            store.job(child_id)["params"]["source_snapshot"]["source_version_id"]
            == first_version["id"]
        )
        store.update_job(child_id, state="running")
        queue.process(child_id)
        assert store.job(child_id)["state"] == "completed"
        enhanced = store.one("SELECT id FROM transcript_results WHERE job_id=?", (child_id,))
        version_count = store.one("SELECT COUNT(*) n FROM result_versions")["n"]
        queue.process(provider_jobs[0])  # Lost local acknowledgement: reuse durable output.
        assert len(calls) == 3
        assert store.one("SELECT COUNT(*) n FROM result_versions")["n"] == version_count
        assert len(store.all("SELECT id FROM jobs WHERE id=?", (child_id,))) == 1
        assert_history_preserved(store, setup_history)
        review_history = history_rows(store)  # Include the newly produced immutable receipts.

        # Synthetic retained obligations must never become a second paid request or disappear.
        obligation_ids = []
        for state, run in (
            ("interrupted", {"submission_intent": True, "run_name": "synthetic-uncertain"}),
            (
                "completed",
                {
                    "job_id": "synthetic-remote",
                    "remote_state": "SUCCESS",
                    "cleanup_state": "pending",
                },
            ),
        ):
            job = store.add_job(
                control,
                "transcribe",
                {
                    "settings": {
                        "provider_type": "ucloud",
                        "base_url": "https://offline.invalid",
                        "model": "large-v3",
                    },
                    "ucloud_run": run,
                },
            )
            store.update_job(job["id"], state=state)
            obligation_ids.append(job["id"])
        obligations = {identifier: store.job(identifier) for identifier in obligation_ids}
        control_before = store.recording(control)

        client.post(
            f"/api/recordings/{rid}/transcripts/{first['id']}/activate",
            json={"revision": store.recording(rid)["revision"]},
        ).raise_for_status()
        edited = client.patch(
            f"/api/recordings/{rid}/segments/0",
            json={
                "revision": store.recording(rid)["revision"],
                "mode": "word",
                "word_index": 0,
                "text": "Hallo",
            },
        )
        assert (
            edited.status_code == 200 and edited.json()["segments"][0]["text"] == "Hallo København."
        )
        client.patch(
            f"/api/recordings/{rid}/speakers",
            json={
                "revision": store.recording(rid)["revision"],
                "speaker": "S0",
                "name": "Øyvind 🧪",
            },
        ).raise_for_status()
        transcript_revision = store.recording(rid)["revision"]
        for url in (f"/api/projects/{projects[0]['id']}/notes", f"/api/recordings/{rid}/notes"):
            saved = client.patch(
                url, json={"revision": 0, "text": "Literal æ\u0301\nResearch notes 🧪"}
            )
            assert saved.status_code == 200
            assert client.patch(url, json={"revision": 0, "text": "Stale reply"}).status_code == 409
        assert store.recording(rid)["revision"] == transcript_revision
        assert store.recording(rid)["notes"] == "Literal æ\u0301\nResearch notes 🧪"
        compared = client.get(
            f"/api/recordings/{rid}/alignment",
            params={"left_id": first["id"], "right_id": enhanced["id"]},
        )
        assert compared.status_code == 200
        phrases = client.post(
            f"/api/recordings/{rid}/alignment/phrases",
            json={
                "left_id": first["id"],
                "right_id": enhanced["id"],
                "phrase": "København",
            },
        )
        assert phrases.status_code == 200 and phrases.json()["left"] and phrases.json()["right"]
        assert (
            client.get(
                f"/api/recordings/{control}/alignment",
                params={"left_id": first["id"], "right_id": enhanced["id"]},
            ).status_code
            == 404
        )
        working = store.result_versions(rid, first["id"])[0]
        mark(client, rid, working["id"])
        checkpoint("working_marked", rid)
        for mode, text in (("free", "Later æøå 🧪 wording."), ("restore", None)):
            response = client.patch(
                f"/api/recordings/{rid}/segments/0",
                json={
                    "revision": store.recording(rid)["revision"],
                    "mode": mode,
                    "text": text,
                },
            )
            assert response.status_code == 200, response.text
        assert store.recording(rid)["segments"][0]["text"] == "Hej København."
        # ADR 0133: edits of the Working head advance it as the system; the researcher then
        # pins the earlier reviewed version again through the ordinary preview/apply API.
        advanced = store.recording(rid)["result_roles"]["working"]
        assert advanced["version_id"] == store.result_versions(rid, first["id"])[0]["id"]
        mark(client, rid, working["id"])
        assert store.recording(rid)["result_roles"]["working"]["version_id"] == working["id"]

        for trashed in (True, False):
            response = client.request(
                "DELETE" if trashed else "POST",
                f"/api/recordings/{rid}/transcripts/{first['id']}"
                + ("" if trashed else "/restore"),
                json={"revision": store.recording(rid)["revision"]},
            )
            assert response.status_code == 200, response.text
            assert store.recording(rid)["result_roles"]["working"] is None
            checkpoint("trashed" if trashed else "restored", rid)
        mark(client, rid, working["id"])
        assert (
            store.result_version(rid, first["id"], working["id"])["payload_sha256"]
            == working["payload_sha256"]
        )

        exports = {}
        for scope, params, skipped in (
            ("project", {}, 1),
            ("collection", {"collection": collection["id"]}, 0),
        ):
            response = client.get(
                f"/api/projects/{projects[0]['id']}/export", params={"format": "json", **params}
            )
            assert response.status_code == 200
            with ZipFile(io.BytesIO(response.content)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert (manifest["exported"], manifest["skipped"]) == (1, skipped)
                if skipped:
                    assert [
                        item["recording_id"]
                        for item in manifest["entries"]
                        if item["status"] == "skipped"
                    ] == [missing]
                [entry] = [entry for entry in manifest["entries"] if entry["status"] == "exported"]
                payload = json.loads(archive.read(entry["path"]))
                assert payload["segments"][0]["text"] == "Hallo København."
                assert payload["speakers"]["S0"] == "Øyvind 🧪"
                assert entry["version_id"] == working["id"]
                exports[scope] = {name: digest(archive.read(name)) for name in archive.namelist()}
        report = client.post(f"/api/projects/{projects[0]['id']}/research-report", json={})
        assert report.status_code == 200
        with ZipFile(io.BytesIO(report.content)) as archive:
            report_manifest = json.loads(archive.read("manifest.json"))
            assert report_manifest["versions"]
        client.post("/api/activity/clear").raise_for_status()
        for identifier in obligation_ids:
            assert client.post("/api/activity/hide", json={"job_id": identifier}).status_code == 409
            assert store.job(identifier) == obligations[identifier]
        assert store.recording(control) == control_before
        assert jobs._remote_open(
            obligations[obligation_ids[0]]["params"]["ucloud_run"], "interrupted"
        )
        assert jobs._remote_open(
            obligations[obligation_ids[1]]["params"]["ucloud_run"], "completed"
        )
        assert_history_preserved(store, review_history)
        checkpoint("before_backup_and_reopen", rid)
        with store.connection() as db:
            before = table_inventory(db)
        backup = client.post("/api/library/backups", json={})
        assert backup.status_code == 201, backup.text
        backup_folder = Path(backup.json()["folder"])
        backup_manifest = json.loads((backup_folder / "manifest.json").read_text(encoding="utf-8"))
        assert backup_manifest["integrity"] == "ok" and backup_manifest["schema"] == SCHEMA_VERSION
        assert {
            item["recording_id"]: item["sha256"] for item in backup_manifest["originals"]
        } == originals
        with closing(sqlite3.connect(backup_folder / "library.sqlite3")) as db:
            assert table_inventory(db) == before
        assert all(
            b"synthetic-memory-only" not in path.read_bytes() for path in backup_folder.iterdir()
        )

    restarted = create_app(root, start_worker=False, credentials=credentials)
    store = restarted.state.store
    with TestClient(restarted, headers={"X-CBS-Request": "1"}) as client:
        with store.connection() as db:
            assert table_inventory(db) == before
        assert_history_preserved(store, review_history)
        assert store.recording(control) == control_before
        assert store.recording(rid)["result_roles"]["working"]["version_id"] == working["id"]
        for target, expected in originals.items():
            assert digest((store.directory(target) / "original.wav").read_bytes()) == expected
        listed = {job["id"]: job for job in client.get("/api/jobs").json()}
        for identifier in obligation_ids:
            assert identifier in listed and store.job(identifier) == obligations[identifier]
            assert client.post("/api/activity/hide", json={"job_id": identifier}).status_code == 409
        # This existing SQLite-derived wire field is 1; Activity consumes its truth value.
        assert listed[obligation_ids[0]]["ucloud_submission_uncertain"] == 1
        assert listed[obligation_ids[1]]["ucloud_cleanup_state"] == "pending"
        assert len(calls) == 3
        checkpoint("reopened", rid)
    evidence = {
        "fixture_origin_commit": "4291fc4e6ec4efbe0621cf7599e120d6ed501b13",
        "scope": (
            "synthetic API/Store; workers disabled; no codec/browser/inference/remote acceptance"
        ),
        "projects": 2,
        "recordings": len(recording_ids),
        "original_sha256": originals,
        "processing_calls": calls,
        "checkpoints": checkpoints,
        "tables": before,
        "preexisting_history": {
            stage: {
                table: {
                    key: digest(json.dumps(row, sort_keys=True, ensure_ascii=False).encode())
                    for key, row in rows.items()
                }
                for table, rows in baseline.items()
            }
            for stage, baseline in (
                ("before_processing", setup_history),
                ("before_review", review_history),
            )
        },
        "preexisting_history_unchanged": ["after_processing", "after_review", "after_reopen"],
        "export_sha256": exports,
        "obligations": {
            identifier: {
                "state": job["state"],
                "journal_sha256": digest(
                    json.dumps(job["params"]["ucloud_run"], sort_keys=True).encode()
                ),
            }
            for identifier, job in obligations.items()
        },
        "backup_format": backup_manifest["format"],
        "backup_includes_media_bodies": False,
        "library_reopen_rows_identical": True,
    }
    (tmp_path / "preservation-evidence.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8"
    )


def test_fresh_admission_complete_package_and_isolated_restore(
    short_preservation_root, credentials, wav_bytes, monkeypatch
):
    """Real admission/decoder/contained restore; only the API response is synthetic.

    Complete means the implemented portable inventory, not model/runtime restoration
    or verification of every historical transformation. No live HTTP/GPU/cloud call.
    """
    from admission_support import inspect_and_materialize

    from cbs_transcriber import libraries, original_inspection, provider_capture, restored_owners
    from cbs_transcriber.store import Store

    case = short_preservation_root
    source_root, restored_root = case / "s", case / "r"
    monkeypatch.setattr(libraries, "registry_path", lambda: case / "registry.json")
    calls = []
    token = "synthetic-api-memory-only"

    def transcribe(path, settings, key):
        assert path.is_file() and path.stat().st_size > 0 and key == token
        calls.append(digest(path.read_bytes()))
        return {
            "segments": [
                {
                    "text": "Hej København.",
                    "start": 0,
                    "end": 2,
                    "words": [
                        {"word": "Hej", "start": 0.1, "end": 0.7},
                        {"word": "København.", "start": 0.8, "end": 1.9},
                    ],
                }
            ]
        }

    monkeypatch.setattr(providers, "transcribe_file", transcribe)
    app = create_app(source_root, start_worker=False, credentials=credentials)
    source, queue = app.state.store, app.state.queue
    libraries.remember(source_root)  # The only registry is the monkeypatched disposable file.
    with source.library_lock(), TestClient(app, headers={"X-CBS-Request": "1"}) as client:
        client.put("/api/editing-settings", json={"realign_edits": False}).raise_for_status()
        projects = [
            source.create_project("Fresh A"),
            source.create_project("Control B", "protected"),
        ]
        recordings = [
            source.create_recording(project["id"], "synthetic.wav") for project in projects
        ]
        original_hashes = {}
        for index, recording in enumerate(recordings):
            folder = source.directory(recording["id"])
            folder.mkdir(parents=True)
            body = wav_bytes[:-2] + index.to_bytes(2, "little")
            (folder / "original.wav").write_bytes(body)
            original_hashes[recording["id"]] = digest(body)
        fresh_id, control_id = [row["id"] for row in recordings]
        source.import_transcript(
            control_id, b'{"segments":[{"text":"Older control wording."}]}', "control.json", 0
        )
        client.patch(
            f"/api/recordings/{control_id}/segments/0",
            json={"revision": 1, "mode": "free", "text": "Current control wording."},
        ).raise_for_status()
        source.save_notes("recordings", control_id, "Older control notes.", 0)
        source.save_notes("recordings", control_id, "Current control notes 🧪", 1)
        baseline = history_rows(source)
        control_before = source.recording(control_id)
        profile = providers.save_profile(
            source,
            credentials,
            "Synthetic API",
            providers.Settings(model="whisper-1", capability="words", language="da"),
            api_key=token,
            remember=False,
        )
        response = client.post(
            "/api/transcriptions",
            json={"recording_ids": [fresh_id], "provider_ids": [profile["id"]]},
        )
        assert response.status_code == 202, response.text
        [accepted] = response.json()
        assert "input_intent" in accepted["params"] and not calls
        [materialized] = inspect_and_materialize(source, credentials, [accepted["id"]])
        assert "input_intent" not in materialized["params"]
        assert materialized["params"]["audio_input"]["policy"] == "original-v1"
        assert materialized["params"]["provider_artifact_policy"] == provider_capture.POLICY
        acceptance = deepcopy(materialized["params"]["input_acceptance"])
        assert original_inspection.read(source, fresh_id)["state"] == "ready"
        assert (
            source.recording(fresh_id)["status"] == "preparing"
        )  # Original readiness is independent.
        source.update_job(accepted["id"], state="running")
        queue.process(accepted["id"])
        completed = source.job(accepted["id"])
        assert completed["state"] == "completed" and len(calls) == 1
        assert completed["params"]["input_acceptance"] == acceptance
        assert completed["params"]["audio_preparations"]
        [result] = source.results(fresh_id)
        creation = source.result_versions(fresh_id, result["id"])[0]
        assert creation["context"]["provider_artifacts"]["policy"] == provider_capture.POLICY
        assert_history_preserved(source, baseline)
        assert source.recording(control_id) == control_before
        baseline = history_rows(source)  # Protect the new creation receipt across review, too.

        edited = client.patch(
            f"/api/recordings/{fresh_id}/segments/0",
            json={
                "revision": source.recording(fresh_id)["revision"],
                "mode": "word",
                "word_index": 0,
                "text": "Hallo",
            },
        )
        assert edited.status_code == 200, edited.text
        working = source.result_versions(fresh_id, result["id"])[0]
        current = source.recording(fresh_id)
        mark_body = {
            "version_id": working["id"],
            "revision": current["revision"],
            "role_revision": current["result_roles"]["revision"],
        }
        preview = client.post(f"/api/recordings/{fresh_id}/working-preview", json=mark_body)
        assert preview.status_code == 200, preview.text
        client.post(
            f"/api/recordings/{fresh_id}/working",
            json={**mark_body, "fingerprint": preview.json()["fingerprint"]},
        ).raise_for_status()
        client.patch(
            f"/api/recordings/{fresh_id}/segments/0",
            json={
                "revision": source.recording(fresh_id)["revision"],
                "mode": "free",
                "text": "New current wording after the historical Working pin.",
            },
        ).raise_for_status()
        latest = source.result_versions(fresh_id, result["id"])[0]
        assert latest["id"] != working["id"]
        assert latest["payload_sha256"] != working["payload_sha256"]
        # ADR 0133: that edit advanced Working to the new head; pin the historical one again.
        current = source.recording(fresh_id)
        assert current["result_roles"]["working"]["version_id"] == latest["id"]
        mark_body = {
            "version_id": working["id"],
            "revision": current["revision"],
            "role_revision": current["result_roles"]["revision"],
        }
        preview = client.post(f"/api/recordings/{fresh_id}/working-preview", json=mark_body)
        assert preview.status_code == 200, preview.text
        client.post(
            f"/api/recordings/{fresh_id}/working",
            json={**mark_body, "fingerprint": preview.json()["fingerprint"]},
        ).raise_for_status()
        client.patch(
            f"/api/recordings/{fresh_id}/notes",
            json={"revision": 0, "text": "Fresh reviewed notes æ\u0301\n🧪"},
        ).raise_for_status()
        # A source remote obligation is evidence, never remote authority for the restored copy.
        remote = source.add_job(
            control_id,
            "transcribe",
            {
                "settings": {
                    "provider_type": "ucloud",
                    "base_url": "https://offline.invalid",
                    "model": "large-v3",
                },
                "ucloud_run": {"submission_intent": True},
            },
        )
        source.update_job(remote["id"], state="interrupted")
        assert_history_preserved(source, baseline)
        assert source.recording(control_id) == control_before
        immutable = history_rows(source)
        jobs_before = source.all("SELECT * FROM jobs ORDER BY id")
        projects_before = source.all("SELECT * FROM projects ORDER BY id")
        recordings_before = source.all("SELECT * FROM recordings ORDER BY id")
        providers_before = source.all("SELECT * FROM providers ORDER BY id")

        def recording_bodies(store):
            observed, total, count = {}, 0, 0
            for row in recordings:
                directory = store.directory(row["id"])
                observed[row["id"]] = {}
                for path in directory.rglob("*"):
                    assert not path.is_symlink() and not path.is_junction()
                    if not path.is_file():
                        continue
                    count += 1
                    size = path.stat().st_size
                    assert count <= 100 and 0 <= size <= 2 * 1024**2 - total
                    with path.open("rb") as stream:
                        body = stream.read(size + 1)
                    assert len(body) == size
                    total += size
                    observed[row["id"]][path.relative_to(directory).as_posix()] = body
            return observed

        source_files = recording_bodies(source)
        with source.connection() as db:
            before_tables = table_inventory(db)

        response = client.post(
            "/api/library/portable-backups", json={"destination": str(case / "p")}
        )
        assert response.status_code == 201, response.text
        captured = response.json()
        creator = json.loads(
            (source_root / "portable-captures" / (captured["backup_id"] + ".json")).read_text()
        )
        assert creator["commit_sha256"] == captured["commit_sha256"]
        # Retain the creator reference separately from the mutable package storage.
        (case / "trusted-receipt.json").write_text(json.dumps(creator), encoding="utf-8")
        trusted = json.loads((case / "trusted-receipt.json").read_text())
        package = Path(captured["folder"])
        manifest = json.loads((package / "manifest.json").read_text())
        assert captured["originals"] == 2 and captured["scope"] == "whole_library"
        assert captured["history_provenance"] == "domain_transform_validation_not_checked"
        roles = {item["role"] for item in manifest["files"]}
        assert {"original", "import_source", "processing_input", "provider_response"} <= roles
        assert all(
            token.encode() not in path.read_bytes() for path in (package / "objects").iterdir()
        )
        refused = client.post(
            "/api/library/portable-restore",
            json={
                "folder": str(package),
                "commit_sha256": "0" * 64,
                "target": str(case / "bad"),
            },
        )
        assert refused.status_code == 400 and not (case / "bad").exists()
        assert recording_bodies(source) == source_files
        response = client.post(
            "/api/library/portable-restore",
            json={
                "folder": str(package),
                "commit_sha256": trusted["commit_sha256"],
                "target": str(restored_root),
            },
        )
        assert response.status_code == 201, response.text
        restoration = response.json()
        assert restoration["state"] == "complete" and restoration["registered"]
        assert restoration["historical_jobs"] == "read_only"
        assert restoration["providers"] == "disconnected"
        assert restoration["playback"] == "requires_explicit_preparation"
        assert restoration["history_provenance"] == "domain_transform_validation_not_checked"
        assert libraries.load()["current"] == str(source_root)
        assert libraries.load()["recent"][0] == str(restored_root)
        assert client.app.state.store.root == source_root
        assert_history_preserved(source, immutable)
        assert recording_bodies(source) == source_files
        with source.connection() as db:
            assert table_inventory(db) == before_tables

    restored = Store(restored_root)
    assert_history_preserved(restored, immutable)
    with restored.connection() as db:
        after_tables = table_inventory(db)
    transformed = {"projects", "recordings", "providers", "settings", "restored_owners"}
    assert {key: value for key, value in after_tables.items() if key not in transformed} == {
        key: value for key, value in before_tables.items() if key not in transformed
    }
    assert restored.all("SELECT * FROM jobs ORDER BY id") == jobs_before
    assert restored.all("SELECT * FROM projects ORDER BY id") == [
        {**row, "storage_dir": str(restored_root / "projects" / row["id"])}
        for row in projects_before
    ]
    assert restored.all("SELECT * FROM recordings ORDER BY id") == [
        {**row, "status": "preparing", "original_input": None} for row in recordings_before
    ]
    safe_profile = {
        "provider_type": "custom",
        "base_url": "https://unconfigured.invalid/v1",
        "model": "unconfigured",
        "language": "",
        "capability": "segments",
        "enabled": False,
        "approved_for": [],
        "max_upload_mb": 20,
        "concurrent_chunks": 1,
        "ucloud": None,
        "ucloud_transcriber": None,
    }
    for old, new in zip(
        providers_before, restored.all("SELECT * FROM providers ORDER BY id"), strict=True
    ):
        assert {key: value for key, value in old.items() if key != "value"} == {
            key: value for key, value in new.items() if key != "value"
        }
        assert json.loads(new["value"]) == safe_profile
    [settings] = restored.all("SELECT * FROM settings")
    assert settings["id"] == 2 and json.loads(settings["value"]) == {
        "default_provider_id": min(row["id"] for row in providers_before)
    }
    assert restored.all("SELECT * FROM restored_owners ORDER BY owner_id") == [
        {
            "kind": "job",
            "owner_id": row["id"],
            "restore_id": restoration["operation_id"],
            "source_backup_id": captured["backup_id"],
        }
        for row in jobs_before
    ]
    for row in jobs_before:
        with pytest.raises(restored_owners.HistoricalOwner):
            jobs.JobQueue(restored, credentials).process(row["id"])
    assert len(calls) == 1
    assert (
        restored.recording(fresh_id)["result_roles"]["working"]["payload_sha256"]
        == working["payload_sha256"]
    )
    assert restored.recording(fresh_id)["result_roles"]["working"]["version_id"] == working["id"]
    assert restored.result_versions(fresh_id, result["id"])[0]["id"] == latest["id"]
    assert original_inspection.read(restored, fresh_id) is None
    for recording in recordings:
        assert (
            digest((restored.directory(recording["id"]) / "original.wav").read_bytes())
            == original_hashes[recording["id"]]
        )
    for item in manifest["files"]:
        destination = (
            restored_root / item["relative_path"]
            if item["project_id"] is None
            else (
                restored_root
                / "projects"
                / item["project_id"]
                / "recordings"
                / item["recording_id"]
                / item["relative_path"]
            )
        )
        assert digest(destination.read_bytes()) == item["object"]
    preserved = restored_root / "preservation" / restoration["operation_id"] / "source.sqlite3"
    assert digest(preserved.read_bytes()) == manifest["database"]["object"]
    with closing(sqlite3.connect(preserved)) as db:
        assert table_inventory(db) == before_tables
    assert recording_bodies(source) == source_files
    (case / "fresh-portable-evidence.json").write_text(
        json.dumps(
            {
                "fixture_origin_commit": "778a5b67ac76d3840fe463196e3fc76676b9fb20",
                "mock_api_calls": len(calls),
                "input_acceptance_sha256": digest(json.dumps(acceptance, sort_keys=True).encode()),
                "original_sha256": original_hashes,
                "working_payload_sha256": working["payload_sha256"],
                "backup_id": captured["backup_id"],
                "trusted_commit_sha256": trusted["commit_sha256"],
                "source_database_sha256": manifest["database"]["object"],
                "file_mappings": len(manifest["files"]),
                "captured_roles": sorted(roles),
                "quarantined_jobs": len(jobs_before),
                "source_tables": before_tables,
                "allowed_working_transforms": sorted(transformed),
                "history_provenance": restoration["history_provenance"],
                "playback": restoration["playback"],
                "remote_execution": "not_run",
                "model_inference": "mocked",
                "post_restore_processing": "not_run",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
