"""Release profile (ADR 0145): one switch hides unfinished features without stranding data."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import MemoryCredentials
from fastapi.testclient import TestClient
from test_annotations_api import annotation_items as annotation_items
from test_annotations_api import create_scope

from cbs_transcriber import enhancement, providers, release_profile, routing
from cbs_transcriber.app import create_app
from cbs_transcriber.transcripts import normalize_segments

STATIC = Path(release_profile.__file__).parent / "static"


def app_client(root, release, credentials=None):
    app = create_app(
        root, start_worker=False, credentials=credentials or MemoryCredentials(), release=release
    )
    return TestClient(app, headers={"X-CBS-Request": "1"})


@pytest.fixture
def mvp(tmp_path, credentials):
    with app_client(tmp_path, "mvp", credentials) as client:
        yield client


def refused(response, feature):
    assert response.status_code == 403, response.text
    assert response.json() == {
        "code": "feature_disabled",
        "feature": feature,
        "detail": f"{release_profile.FEATURES[feature]} is not available in this version of "
        "CBS Transcriber.",
    }


def test_resolution_order_cli_environment_packaged_then_full(tmp_path):
    packaged = tmp_path / "release.json"
    assert release_profile.resolve(environ={}, path=packaged).name == "full"
    release_profile.write("mvp", "MVP rc1", packaged)
    assert json.loads(packaged.read_text()) == {"profile": "mvp", "label": "MVP rc1"}
    build = release_profile.resolve(environ={}, path=packaged)
    assert (build.name, build.label, build.disabled) == (
        "mvp",
        "MVP rc1",
        set(release_profile.FEATURES),
    )
    # The environment overrides the packaged choice; the command line overrides both.
    assert (
        release_profile.resolve(environ={"CBS_TRANSCRIBER_PROFILE": "full"}, path=packaged).name
        == "full"
    )
    assert (
        release_profile.resolve(
            "mvp", environ={"CBS_TRANSCRIBER_PROFILE": "full"}, path=packaged
        ).label
        == "MVP rc1"
    )
    assert (
        release_profile.resolve(
            "full", environ={"CBS_TRANSCRIBER_PROFILE": "mvp"}, path=packaged
        ).disabled
        == frozenset()
    )
    with pytest.raises(ValueError, match="Unknown release profile 'beta'"):
        release_profile.resolve(environ={"CBS_TRANSCRIBER_PROFILE": "beta"}, path=packaged)
    packaged.write_text('{"profile": "mvp", "extra": 1}')
    with pytest.raises(ValueError, match="invalid"):
        release_profile.resolve(environ={}, path=packaged)
    with pytest.raises(ValueError, match="release label"):
        release_profile.write("mvp", "<script>", packaged)


def test_main_ships_full_and_one_command_builds_the_mvp_variant(tmp_path):
    assert release_profile.packaged().name == "full"
    target = tmp_path / "release.json"
    command = [
        sys.executable,
        "-m",
        "cbs_transcriber.release_profile",
        "mvp",
        "--label",
        "MVP rc1",
        "--file",
        str(target),
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=True)
    assert "Release profile mvp written" in done.stdout
    assert release_profile.packaged(target).public() == {
        "profile": "mvp",
        "label": "MVP rc1",
        "disabled": sorted(release_profile.FEATURES),
        "features": sorted(release_profile.FEATURES),
    }
    refused = subprocess.run([*command[:3], "beta", "--file", str(target)], capture_output=True)
    assert refused.returncode == 2


def test_unknown_profile_refuses_app_start(tmp_path, monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "everything")
    with pytest.raises(ValueError, match="Unknown release profile"):
        create_app(tmp_path, start_worker=False, credentials=MemoryCredentials())
    assert not (tmp_path / "library.sqlite3").exists()


@pytest.mark.parametrize(
    "release,environment,expected",
    [(None, None, "full"), ("mvp", None, "mvp"), (None, "mvp", "mvp"), ("full", "mvp", "full")],
)
def test_features_endpoint_and_page_carry_the_effective_profile(
    tmp_path, monkeypatch, release, environment, expected
):
    if environment:
        monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", environment)
    with app_client(tmp_path, release) as client:
        features = client.get("/api/features").json()
        page = client.get("/").text
    hidden = sorted(release_profile.PROFILES[expected])
    assert features["profile"] == expected and features["disabled"] == hidden
    assert features["features"] == sorted(release_profile.FEATURES)
    assert f'<html data-profile="{expected}" data-off="{" ".join(hidden)}"' in page


GUARDED = [
    ("post", "/api/recordings/r/retranscriptions", "retranscribe"),
    ("post", "/api/projects/p/codebook/versions", "topics"),
    ("post", "/api/recordings/r/annotation-runs", "topics"),
    ("post", "/api/recordings/r/annotation-runs/import", "topics"),
    ("post", "/api/recordings/r/annotation-runs/a/items", "topics"),
    ("patch", "/api/recordings/r/annotation-runs/a/items/i", "topics"),
    ("post", "/api/projects/p/research-report", "research_report"),
    ("post", "/api/enhancement-api/check", "cbs_runtime"),
    ("post", "/api/enhancement-api/prepare", "cbs_runtime"),
    ("post", "/api/enhancement-cloud/check", "cbs_runtime"),
    ("put", "/api/providers/p/runtime-language", "cbs_runtime"),
    ("put", "/api/runtime-batches/settings", "cbs_runtime"),
    ("put", "/api/enhancement-routing", "routing_rules"),
    ("post", "/api/recordings/r/transcripts/t/wording-preview", "wording_adoption"),
    ("post", "/api/recordings/r/transcripts/t/wording-adopt", "wording_adoption"),
    ("post", "/api/library/portable-simple/prepare", "cloud_backup"),
    ("post", "/api/library/portable-simple/folder", "cloud_backup"),
    ("post", "/api/library/portable-simple/admit", "cloud_backup"),
    ("put", "/api/library/portable-storage", "cloud_backup"),
    ("post", "/api/library/portable-upload", "cloud_backup"),
    ("put", "/api/library/portable-retention", "cloud_backup"),
    ("post", "/api/library/portable-retention/preview", "cloud_backup"),
    ("post", "/api/library/portable-retention/apply", "cloud_backup"),
    ("post", "/api/library/portable-restore", "restore_ui"),
]

RECOVERY = [
    ("post", "/api/jobs/j/cancel"),
    ("post", "/api/jobs/j/retry"),
    ("post", "/api/jobs/j/reconcile"),
    ("post", "/api/jobs/j/cleanup-ucloud"),
    ("post", "/api/runtime-batches/b/cancel"),
    ("post", "/api/runtime-batches/b/retry"),
    ("post", "/api/runtime-batches/b/reconcile"),
    ("post", "/api/runtime-batches/b/cleanup"),
    ("post", "/api/runtime-batches/b/discard-preview"),
    ("post", "/api/runtime-batches/b/discard"),
    ("post", "/api/runtime-preparations/o/cancel"),
    ("post", "/api/runtime-preparations/o/retry"),
    ("post", "/api/runtime-preparations/o/reconcile"),
    ("post", "/api/runtime-preparations/o/cleanup"),
    ("post", "/api/remote-releases/batch/b/retry"),
    ("post", "/api/input-intents/i/retry"),
    ("post", "/api/input-intents/i/prepare"),
    ("post", "/api/library/portable-simple/stop"),
    ("post", "/api/library/portable-simple/o/abandon"),
    ("post", "/api/library/portable-upload/o/folder-preview"),
    ("post", "/api/library/portable-upload/o/reconcile-folder"),
    ("post", "/api/library/portable-retention/e/b/retry"),
    ("post", "/api/library/portable-backup-check"),
]


def test_local_backup_and_its_receipt_are_never_guarded():
    for method, path in (
        ("POST", "/api/library/portable-backups"),
        ("GET", "/api/library/portable-backups"),
        ("GET", "/api/library/portable-backups/b/verify"),
        ("POST", "/api/library/portable-backup-volume-hint"),
    ):
        assert release_profile.guarded(method, path) is None


@pytest.mark.parametrize("method,path,feature", GUARDED)
def test_mvp_refuses_new_work_for_each_hidden_feature_before_its_handler(
    mvp, method, path, feature
):
    assert release_profile.guarded(method.upper(), path) == feature
    refused(getattr(mvp, method)(path, json={}), feature)


@pytest.mark.parametrize("method,path", RECOVERY)
def test_recovery_controls_for_existing_work_stay_open(mvp, method, path):
    assert release_profile.guarded(method.upper(), path) is None
    response = getattr(mvp, method)(path, json={})
    assert response.status_code != 403 or "feature_disabled" not in response.text


def test_full_profile_keeps_every_guarded_route(client):
    """Full is unchanged: the same requests reach their handlers (404/400/422, never 403)."""
    for method, path, _ in GUARDED:
        if "enhancement-api" in path or "enhancement-cloud" in path or "portable" in path:
            continue  # Their handlers may read saved UCloud settings; covered by their suites.
        response = getattr(client, method)(path, json={})
        assert response.status_code != 403, (path, response.text)
    project = client.app.state.store.create_project("Report")
    response = client.post(f"/api/projects/{project['id']}/research-report", json={})
    assert response.status_code == 200
    assert client.put("/api/enhancement-routing", json={"word_minutes": 4}).status_code == 200


@pytest.fixture
def timed(seeded):
    # Admission requires the caller-held serving lease, as in the running app.
    with seeded[1].library_lock():
        yield seeded


@pytest.fixture
def seeded(mvp, wav_bytes):
    store = mvp.app.state.store
    project = store.create_project("MVP")
    recording = store.create_recording(project["id"], "interview.wav")
    directory = store.directory(recording["id"])
    directory.mkdir(parents=True)
    (directory / "original.wav").write_bytes(wav_bytes)
    store.execute("UPDATE recordings SET status='ready',duration=3 WHERE id=?", (recording["id"],))
    words = [{"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, w in enumerate("abc")]
    segments = normalize_segments(
        [
            {"text": "a b c", "start": 0, "end": 1.5, "words": words},
            {"text": "Farvel", "start": 1.5, "end": 3},
        ]
    )
    result = store.add_result(recording["id"], segments, {}, "Native", {"capability": "words"})
    return mvp, store, recording, result


def accepted(store, job_id):
    return store.job(job_id)["params"]["input_intent"]["accepted"]


def test_mvp_runs_timing_and_speakers_on_this_computer_only(timed, monkeypatch):
    client, store, recording, result = timed
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **k: {"ready": True})
    url = f"/api/recordings/{recording['id']}/enhancements"
    body = {"result_id": result["id"], "revision": 1, "runtime_python": sys.executable}
    refused(client.post(url, json=body | {"execution": "ucloud"}), "ucloud_enhancement")
    # Automatic never reaches UCloud: it is frozen as this computer, with no connection.
    response = client.post(url, json=body | {"execution": "auto", "diarize": True})
    assert response.status_code == 202, response.text
    choice = accepted(store, response.json()["id"])
    assert choice["options"]["execution"] == "local"
    assert choice["routing"]["connection"] is None


def test_mvp_passage_timing_never_routes_a_large_selection_to_ucloud(tmp_path, monkeypatch):
    import test_repair_bundles as fixtures

    store, recording, source = fixtures.study.__wrapped__(tmp_path)[:3]
    monkeypatch.setattr(enhancement, "require_local_runtime", lambda *_: None)
    routing.save_rules(store, routing.Rules(word_minutes=0, fallback="stop"))
    units = [{"ids": ["1"], "start": 2, "end": 5}, {"ids": ["4"], "start": 9, "end": 12}]
    options, extra = routing.repair_route(
        store,
        None,
        recording,
        source,
        units,
        enhancement.EnhancementOptions(device="cpu"),
        cloud=False,
    )
    assert options.execution == "local"
    assert extra["repair_routing"]["place"] == "local"
    assert "this version runs word timing here" in extra["routing"]


def test_mvp_transcription_follow_ups_stay_local_and_hidden_providers_are_refused(
    timed, monkeypatch
):
    client, store, recording, _ = timed
    credentials = client.app.state.credentials
    openai = providers.save_profile(
        store,
        credentials,
        "OpenAI",
        providers.Settings(model="gpt-transcribe", capability="json"),
        api_key="synthetic-key",
        remember=False,
    )
    providers.save_profile(
        store,
        credentials,
        "UCloud",
        providers.Settings(
            provider_type="ucloud",
            base_url="https://cloud.invalid",
            model="large-v3",
            language="da",
            ucloud_transcriber={
                "project_id": "synthetic-project",
                "storage_path": "/drive/cbs",
                "application": {"name": "transcriber", "version": "1.12"},
                "product": {"id": "gpu-nvidia-b200-1-gpu", "category": "gpu", "provider": "ucloud"},
            },
        ),
        api_key="synthetic-cloud-token",
        remember=False,
    )
    rows = client.get("/api/providers").json()["providers"]
    runtime = next(p["id"] for p in rows if p["model"].startswith("whisperx/"))
    request = {"recording_ids": [recording["id"]]}
    refused(
        client.post("/api/transcriptions", json=request | {"provider_ids": [runtime]}),
        "cbs_runtime",
    )
    enhancement_options = {"runtime_python": sys.executable, "align": True}
    refused(
        client.post(
            "/api/transcriptions",
            json=request
            | {
                "provider_ids": [openai["id"]],
                "enhancement": enhancement_options | {"execution": "ucloud"},
            },
        ),
        "ucloud_enhancement",
    )
    response = client.post(
        "/api/transcriptions",
        json=request
        | {
            "provider_ids": [openai["id"]],
            "requested_features": {"word_timing": True},
            "enhancement": enhancement_options | {"execution": "auto"},
        },
    )
    assert response.status_code == 202, response.text
    choice = accepted(store, response.json()[0]["id"])
    assert choice["enhancement"]["execution"] == "local"
    assert choice["routing"]["connection"] is None
    # Custom and UCloud vLLM setups stay hidden; OpenAI setup is unchanged.
    custom = {
        "name": "Custom",
        "provider_type": "custom",
        "base_url": "https://speech.invalid/v1",
        "model": "custom-model",
    }
    refused(client.post("/api/providers", json=custom), "custom_providers")
    refused(client.put("/api/settings", json=custom), "custom_providers")
    refused(
        client.post("/api/providers", json=custom | {"provider_type": "ucloud-vllm"}),
        "custom_providers",
    )


def test_mvp_free_edit_never_queues_hidden_realignment(seeded, monkeypatch):
    client, store, recording, result = seeded
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **k: {"ready": True})
    response = client.patch(
        f"/api/recordings/{recording['id']}/segments/0/edit-v1",
        json={"mode": "free", "text": "a b c d", "revision": 1, "realign": True},
    )
    assert response.status_code == 200, response.text
    assert store.recording(recording["id"])["segments"][0]["text"] == "a b c d"
    assert not store.all("SELECT id FROM jobs")
    # The full profile still honours the same explicit choice.
    with app_client(store.root, "full") as full:
        response = full.patch(
            f"/api/recordings/{recording['id']}/segments/0/edit-v1",
            json={"mode": "free", "text": "a b c", "revision": 2, "realign": True},
        )
    assert response.status_code == 200, response.text
    assert len(store.all("SELECT id FROM jobs WHERE kind='enhance'")) == 1


def test_existing_hidden_feature_data_stays_readable_and_exportable(
    tmp_path, client, annotation_items
):
    store, projects, recordings, results, _ = annotation_items
    codebooks, codebook, runs, run, _ = create_scope(client, annotation_items)
    retranscribed = store.add_result(
        recordings[0]["id"],
        normalize_segments([{"text": "Ny ordlyd", "start": 0, "end": 1}]),
        {},
        "Local WhisperX large-v3-turbo re-transcription",
        {"kind": "retranscription"},
    )
    with app_client(store.root, "mvp") as mvp:
        assert mvp.get(codebooks).json()[0]["id"] == codebook["id"]
        assert mvp.get(f"{runs}/{run['id']}").status_code == 200
        assert mvp.get(f"{runs}/{run['id']}/items").status_code == 200
        exported = mvp.get(f"{runs}/{run['id']}/export")
        assert exported.status_code == 200
        refused(mvp.post(codebooks, json={}), "topics")
        rid = recordings[0]["id"]
        shown = mvp.get(f"/api/recordings/{rid}").json()
        assert retranscribed["id"] in [r["id"] for r in shown["transcripts"]]
        text = mvp.get(f"/api/recordings/{rid}/export?format=text&result_id={retranscribed['id']}")
        assert text.status_code == 200 and "Ny ordlyd" in text.text
        archive = mvp.get(f"/api/projects/{projects[0]['id']}/export")
        assert archive.status_code == 200


def test_static_feature_markers_match_the_profile_catalog():
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "app.js").read_text(encoding="utf-8")
    styles = (STATIC / "app.css").read_text(encoding="utf-8")
    used = {
        name
        for value in re.findall(r'data-feature="([^"]+)"', markup + script)
        for name in value.split()
    } | set(re.findall(r'featureOn\("([a-z_]+)"\)', script))
    assert used <= set(release_profile.FEATURES)
    assert used == set(release_profile.FEATURES)
    # Every marked feature is hidden by CSS, so later code cannot show it again.
    for name in set(re.findall(r'data-feature="([^"]+)"', markup + script)):
        for feature in name.split():
            assert f'html[data-off~="{feature}"]' in styles, feature


def test_cli_profile_wins_and_is_frozen_only_when_explicit_in_shortcuts(tmp_path, monkeypatch):
    from cbs_transcriber import __main__ as launcher
    from cbs_transcriber import shortcuts

    calls, seen = [], []
    monkeypatch.setattr(
        shortcuts,
        "create_shortcut",
        lambda arguments, *, location: calls.append(arguments) or tmp_path / "launcher",
    )
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "mvp")
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--create-shortcut"])
    launcher.main()
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--create-shortcut", "--profile", "mvp"])
    launcher.main()
    assert calls == [["--port", "8765"], ["--port", "8765", "--profile", "mvp"]]

    def opened(*args, release, **kwargs):
        seen.append(release)
        raise ValueError("synthetic stop before serving")

    monkeypatch.setattr(launcher, "create_app", opened)
    library = ["--no-browser", "--data-dir", str(tmp_path / "library")]
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", "--profile", "full", *library])
    with pytest.raises(SystemExit):
        launcher.main()
    assert seen[-1].name == "full"
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "beta")
    monkeypatch.setattr(sys, "argv", ["cbs-transcriber", *library])
    with pytest.raises(SystemExit) as error:
        launcher.main()
    assert error.value.code == 2 and len(seen) == 1


def test_mvp_keeps_permanent_library_deletion_and_its_recovery(mvp):
    """Library deletion is part of the MVP (ADR 0144): never guarded or marked as hidden."""
    for path in (
        "/api/libraries/delete-preview",
        "/api/libraries/delete",
        "/api/libraries/deletions/d/retry",
    ):
        assert release_profile.guarded("POST", path) is None
        response = mvp.post(path, json={})
        assert response.status_code != 403 or "feature_disabled" not in response.text
    markup = (STATIC / "index.html").read_text(encoding="utf-8")
    panel = re.search(r"<section[^>]*data-library-delete-panel[^>]*>", markup).group(0)
    assert "data-feature" not in panel
    assert "data-feature" not in (STATIC / "library.js").read_text(encoding="utf-8")
