"""Runtime result metadata retains frozen dependency pins across application updates."""

import copy
import json
import wave
from pathlib import Path

import pytest
from test_runtime_batches import environment as environment
from test_runtime_batches import run_batch

from cbs_transcriber import enhancement
from cbs_transcriber.enhancement import run_enhancement as real_enhancement
from cbs_transcriber.transcripts import normalize_segments


@pytest.mark.parametrize("operation", ["asr", "enhancement"])
def test_runtime_result_and_version_keep_the_executed_model_pin(
    environment, monkeypatch, operation
):
    env = environment
    monkeypatch.setattr(enhancement, "run_enhancement", real_enhancement)
    frozen = tuple(env.params["ucloud_api"]["batch_plan"]["identity"]["model_revisions"]["da"])
    current = ("current/new-aligner", "f" * 40)
    assert frozen != current
    monkeypatch.setattr(enhancement, "MODELS", {**enhancement.MODELS, "da": current})

    def fake_ffmpeg(args, cancel):
        with wave.open(str(Path(args[-1])), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0\0" * 48000)

    monkeypatch.setattr(enhancement.media, "ffmpeg", fake_ffmpeg)
    execute = env.drive.worker

    def aligned(folder, lock_fd):
        child = execute(folder, lock_fd)
        (folder / "alignment.json").write_text(
            json.dumps(
                {
                    "passages": [
                        {
                            "id": "0",
                            "words": [
                                {"word": "Hej", "start": 0.1, "end": 0.8, "score": 0.9},
                                {"word": "København.", "start": 1.0, "end": 2.5, "score": 0.9},
                            ],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return child

    env.drive.worker = aligned
    original_id = None
    if operation == "asr":
        job = env.enqueue()
    else:
        project_id = env.store.all("SELECT id FROM projects")[0]["id"]
        recording = env.store.create_recording(project_id, "synthetic.wav")
        rid = recording["id"]
        env.store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (rid,))
        original = env.store.add_result(
            rid,
            normalize_segments([{"text": "Hej København.", "start": 0, "end": 3}]),
            {},
            "Reviewed",
            {},
        )
        original_id = original["id"]
        params = copy.deepcopy(env.params)
        params.pop("transcription")
        params["enhancement"]["transcribe"] = False
        params["source_snapshot"] = env.store.freeze_result(
            rid, original_id, env.store.recording(rid)["revision"]
        )
        job = env.store.add_job(rid, "enhance", params)
    batch = run_batch(env)
    assert batch["state"] == "completed", (batch["message"], env.drive.errors)
    [result] = [r for r in env.store.results(job["recording_id"]) if r["id"] != original_id]
    source = env.store.result(job["recording_id"], result["id"])["source"]
    assert source["model_revisions"]["da"] == {"repo": frozen[0], "revision": frozen[1]}
    version = env.store.result_versions(job["recording_id"], result["id"])[0]
    assert (
        version["context"]["processing"]["model_revisions"]["da"] == source["model_revisions"]["da"]
    )


def test_known_frozen_aliases_match_and_missing_dependencies_remain_unknown():
    pin = ["historical/asr", "a" * 40]
    job = {
        "params": {
            "ucloud_api": {
                "batch_plan": {
                    "identity": {
                        "model_revisions": {
                            "coarse": pin,
                            "whisper/small": pin,
                            "da": ["historical/aligner", "b" * 40],
                        }
                    }
                }
            }
        }
    }
    assert enhancement.asr_model_revisions(["coarse", "da", "speakers"], {}, job=job) == {
        "coarse": {"repo": pin[0], "revision": pin[1]},
        "da": {"repo": "historical/aligner", "revision": "b" * 40},
    }
    job["params"]["ucloud_api"]["batch_plan"]["identity"]["model_revisions"]["whisper/small"] = [
        "other/asr",
        "c" * 40,
    ]
    with pytest.raises(ValueError, match="inconsistent"):
        enhancement.asr_model_revisions(["coarse"], {}, job=job)


@pytest.mark.parametrize(
    "pin",
    [
        None,
        ["model", "main"],
        ["../model", "a" * 40],
        ["safe/model", "A" * 40],
        ["safe/model", "a" * 40, "extra"],
    ],
)
def test_malformed_frozen_dependencies_refuse_without_current_registry_fallback(pin):
    job = {"params": {"ucloud_api": {"batch_plan": {"identity": {"model_revisions": {"da": pin}}}}}}
    with pytest.raises(ValueError, match="revision"):
        enhancement.asr_model_revisions(["da"], {}, job=job)


@pytest.mark.parametrize("remote", [None, {}, {"runtime_id": "legacy"}, {"batch_plan": None}])
def test_no_plan_legacy_jobs_keep_existing_model_metadata(remote):
    expected = enhancement.asr_model_revisions(["da"], {})
    assert (
        enhancement.asr_model_revisions(["da"], {}, job={"params": {"ucloud_api": remote}})
        == expected
    )


@pytest.mark.parametrize(
    "plan",
    [False, 0, "", [], {}, {"identity": None}, {"identity": {"model_revisions": None}}],
)
def test_malformed_present_plans_never_fall_back_to_registry(plan):
    job = {"params": {"ucloud_api": {"batch_plan": plan}}}
    with pytest.raises(ValueError, match="revisions are invalid"):
        enhancement.asr_model_revisions(["da"], {}, job=job)


@pytest.mark.parametrize("remote", [{}, {"batch_plan": None}])
def test_runtime_member_without_plan_never_uses_singleton_metadata(remote):
    job = {"params": {"ucloud_api": remote, "runtime_batch_id": "frozen-owner"}}
    with pytest.raises(ValueError, match="revisions are invalid"):
        enhancement.asr_model_revisions(["da"], {}, job=job)
