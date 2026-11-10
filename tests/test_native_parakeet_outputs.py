"""Observed Parakeet Batch filenames; synthetic audio and mocked UCloud only."""

import hashlib
import json
from copy import deepcopy

import httpx
import pytest
from test_native_batch import batch_remote as batch_remote
from test_native_batch import enqueue, frozen_native_jobs
from test_native_batch import selection as selection
from test_ucloud_native import remote as remote

from cbs_transcriber import native_batch, native_capture, portable_capture

MODEL = "nvidia/parakeet-tdt-0.6b-v3"


@pytest.mark.parametrize("captured", [True, False], ids=["captured", "legacy"])
def test_v84_five_parakeet_outputs_recover_from_same_allocation(
    client, batch_remote, selection, wav_bytes, tmp_path, captured
):
    provider, recordings = selection
    store = client.app.state.store
    batch_remote.store = store
    provider.update(model=MODEL, language="")
    assert client.put("/api/providers/" + provider["id"], json=provider).status_code == 200
    declared = batch_remote.template["status"]["resolvedApplication"]["invocation"]["parameters"]
    next(parameter for parameter in declared if parameter["name"] == "model")["options"] = [
        {"name": MODEL, "value": "parakeet"}
    ]
    batch_remote.template["specification"]["parameters"].pop("language")
    next(p for p in declared if p["name"] == "language").pop("defaultValue", None)
    for index in range(3):
        recording = store.create_recording(recordings[0]["project_id"], f"extra-{index}.wav")
        directory = store.directory(recording["id"])
        directory.mkdir(parents=True)
        (directory / "original.wav").write_bytes(wav_bytes)
        store.execute(
            "UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],)
        )
        recordings.append(recording)
    jobs = (
        enqueue(client, provider, recordings)
        if captured
        else frozen_native_jobs(client, provider, recordings)
    )
    batch_id = jobs[0]["params"]["native_batch_id"]
    members = store.native_batch(batch_id)["members"]
    payloads = {
        member["job_id"]: json.dumps(
            {"segments": [{"text": f"Synthetic member {index}", "start": 0, "end": 1}]}
        ).encode()
        for index, member in enumerate(members)
    }
    ready, downloaded = False, []
    original = batch_remote.handle

    def outputs(request):
        if request.url.host == "files.invalid" and request.url.path == "/download":
            assert "authorization" not in request.headers
            return httpx.Response(200, content=payloads[request.url.params["member"]])
        if request.url.path == "/api/files/download":
            leaf = json.loads(request.content)["items"][0]["id"].rsplit("/", 1)[-1]
            member_id = leaf.removesuffix("_parakeet.json")
            assert member_id in payloads
            downloaded.append(member_id)
            return httpx.Response(
                200,
                json={
                    "responses": [
                        {"endpoint": "https://files.invalid/download?member=" + member_id}
                    ]
                },
            )
        if request.url.path == "/api/files/browse":
            folder = request.url.params["path"]
            names = ["remote-job.zip", "transcribe.log"]
            if ready:
                names += [member["job_id"] + "_parakeet.json" for member in members]
            return httpx.Response(200, json={"items": [{"id": folder + "/" + n} for n in names]})
        return original(request)

    batch_remote.handle = outputs
    queue = client.app.state.queue
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = batch_id
    with pytest.raises(ValueError, match="5 of 5 batch outputs could not be imported"):
        queue.process(claimed["id"])
    assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 5
    assert not downloaded and not any(store.results(r["id"]) for r in recordings)
    frozen = deepcopy([store.job(job["id"])["params"]["settings"] for job in jobs])
    submissions = {
        job["id"]: deepcopy(store.job(job["id"])["params"].get("native_artifact_capture"))
        for job in jobs
    }
    assert all(
        (capture is not None and len(capture["submissions"]) == 1) if captured else capture is None
        for capture in submissions.values()
    )
    ready = True
    assert client.post("/api/jobs/" + jobs[0]["id"] + "/retry", json={}).status_code == 200
    claimed = store.claim_job("transcribe")
    queue.active_batch_id = batch_id
    queue.process(claimed["id"])
    batch = store.native_batch(batch_id)
    assert batch["state"] == "completed"
    assert batch["params"]["ucloud_run"]["output_model"] == "parakeet"
    assert len(batch_remote.submissions) == 1 and len(batch_remote.uploads) == 5
    assert set(downloaded) == set(payloads) and len(downloaded) == 5
    assert [store.job(job["id"])["params"]["settings"] for job in jobs] == frozen
    for job in jobs:
        saved = store.job(job["id"])
        assert saved["state"] == "completed"
        [result] = store.results(job["recording_id"])
        version = store.one(
            "SELECT context FROM result_versions WHERE result_id=? AND sequence=1", (result["id"],)
        )
        context = json.loads(version["context"])
        raw = store.directory(job["recording_id"]) / f"ucloud-{job['id']}.json"
        assert raw.read_bytes() == payloads[job["id"]]
        if captured:
            capture = saved["params"]["native_artifact_capture"]
            assert capture["submissions"] == submissions[job["id"]]["submissions"]
            assert capture["output"]["match"] == {
                "remote_name": job["id"] + "_parakeet.json",
                "model": "parakeet",
                "language": "",
            }
            observed = capture["output"]["response"]["artifacts"]["raw_response"]
            assert observed["sha256"] == hashlib.sha256(payloads[job["id"]]).hexdigest()
            assert native_capture.validate(capture, saved) == capture
            bound = context["native_artifacts"]
            assert (
                native_capture.unbound(
                    bound,
                    recording_id=job["recording_id"],
                    job_id=job["id"],
                    creation=bound["creation"],
                )
                == capture
            )
        else:
            assert "native_artifact_capture" not in saved["params"]
            assert "native_artifacts" not in context
    # Reprocessing does not download again or duplicate results/receipts.
    queue.process(claimed["id"])
    assert len(downloaded) == 5 and len(batch_remote.submissions) == 1
    assert all(len(store.results(recording["id"])) == 1 for recording in recordings)
    if captured:
        # Direct process() calls leave the worker-loop's idle transition to this harness.
        queue.active_batch_id = None
        # The sealed portable checker must accept these same bound suffixless observations.
        package = portable_capture.capture_library(
            store, queue, tmp_path / "packages", gate=portable_capture.CaptureGate()
        )
        assert package["folder"]


@pytest.mark.parametrize("language", ["", "da"])
def test_v164_parakeet_capture_accepts_exact_suffixless_output(language):
    native_capture._match(
        {"remote_name": "member_parakeet.json", "model": "parakeet", "language": language},
        {
            "scope": "batch",
            "input": {"remote_name": "member.wav"},
            "output_selector": {"model": "parakeet", "language": language},
        },
    )


@pytest.mark.parametrize(
    ("model", "names"),
    [
        ("parakeet", ["other_parakeet.json"]),
        ("parakeet", ["member_parakeet-v2.json"]),
        ("parakeet", ["member_parakeet_merged.json"]),
        ("parakeet", ["member_parakeet.txt"]),
        ("large-v3", ["member_parakeet.json"]),
        (MODEL, ["member_parakeet.json"]),
        ("parakeet-v2", ["member_parakeet.json"]),
        ("parakeet", ["member_parakeet.json", "member_parakeet_da.json"]),
        ("parakeet", ["member_parakeet.json", "member.json"]),
    ],
)
def test_v84_parakeet_output_identity_and_ambiguity_stay_strict(model, names):
    with pytest.raises(ValueError, match="unambiguous"):
        native_batch._download(
            None,
            "https://cloud.invalid",
            {"/owned/out/" + name for name in names},
            "/owned/out",
            "member.wav",
            model,
            "",
        )
    if len(names) == 1:
        with pytest.raises(ValueError, match="native artifact"):
            native_capture._match(
                {"remote_name": names[0], "model": model, "language": ""},
                {
                    "scope": "batch",
                    "input": {"remote_name": "member.wav"},
                    "output_selector": {"model": model, "language": ""},
                },
            )


def test_v84_parakeet_output_outside_owned_folder_is_refused():
    with pytest.raises(ValueError):
        native_batch._download(
            None,
            "https://cloud.invalid",
            {"/foreign/out/member_parakeet.json"},
            "/owned/out",
            "member.wav",
            "parakeet",
            "",
        )
