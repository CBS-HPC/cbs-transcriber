"""Remove from Activity hides finished cards; requests group into one card (V162)."""

import copy
import io
import json
import uuid
import wave

import pytest
from playwright.sync_api import expect
from test_browser import create_project
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.store import now

pytestmark = pytest.mark.browser

CONSENT = (
    "This job requires preparation consent. Start a new request after the runtime "
    "is prepared and confirmed."
)


def finished_job(store, recording_id, kind, state, message, params=None):
    # Inserted directly as finished, so the running local workers never claim them.
    identifier = uuid.uuid4().hex
    stamp = now()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,progress,message,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (identifier, recording_id, kind, state, 1, message, json.dumps(params or {}), stamp, stamp),
    )
    return identifier


def test_removed_job_stays_removed_and_obligations_keep_no_remove(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Activity removal")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    done = finished_job(store, rid, "media", "completed", "Converted for playback")
    finished_job(
        store,
        rid,
        "transcribe",
        "completed",
        "Transcript saved",
        {
            "settings": {"provider_type": "ucloud", "base_url": "https://cloud.invalid"},
            "ucloud_run": {
                "job_id": "synthetic",
                "remote_state": "SUCCESS",
                "cleanup_state": "pending",
            },
        },
    )
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    jobs = page.locator("#jobs .job")
    expect(jobs).to_have_count(2)
    obligation = jobs.filter(has_text="Transcript saved")
    expect(obligation.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    expect(obligation).to_contain_text("Retry remote cleanup")
    remove = jobs.filter(has_text="Converted for playback").get_by_role(
        "button", name="Remove from Activity", exact=True
    )
    expect(remove).to_have_text("×")
    expect(remove).to_have_attribute("title", "Remove from Activity. Nothing is deleted.")
    with page.expect_response("**/api/activity/hide") as response:
        remove.click()
    assert response.value.ok
    expect(jobs).to_have_count(1)
    expect(jobs.filter(has_text="Converted for playback")).to_have_count(0)

    page.reload()
    page.locator("#activity-open").click()
    expect(jobs).to_have_count(1)
    expect(jobs.filter(has_text="Transcript saved")).to_be_visible()
    # Nothing is deleted, and a later change to the removed job shows it again.
    assert store.one("SELECT COUNT(*) AS n FROM jobs")["n"] == 2
    store.update_job(done, message="Converted again")
    expect(jobs.filter(has_text="Converted again")).to_have_count(1)


def intent(identifier, name, request, *, error="needs_confirmation", resolved=True):
    terminal = error is not None
    preparation = {
        "kind": "intent",
        "phase": "terminal" if terminal else "waiting",
        "epoch": 2,
        "request_id": request,
        "source_state": "ready" if terminal else "running",
        "native_group_size": 0,
        "native_group_waiting": 0,
        "native_group_resolved": resolved,
        "can_cancel": not terminal,
        "can_retry": error == "source_invalid",
        "error_code": error,
        "can_prepare": error == "needs_confirmation",
        "prepare_preview": {
            "scope": "recording",
            "count": 1,
            "fingerprint": identifier[:1] * 64,
            "recordings": [{"recording_id": "f" * 32, "name": name, "provider_name": "UCloud"}],
        }
        if error == "needs_confirmation"
        else None,
        "prepare_blocker": None,
        "continuation": None,
    }
    return {
        "id": identifier,
        "recording_id": uuid.uuid4().hex,
        "kind": "transcribe",
        "state": "cancelled" if error == "cancelled" else "failed" if terminal else "queued",
        "progress": 0,
        "message": CONSENT if error == "needs_confirmation" else "Synthetic preparation outcome.",
        "recording_name": name,
        "provider_name": "UCloud Røst v3 (CBS Runtime)",
        "model": "syvai/hviske-v3-conversation",
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:00+00:00",
        "input_preparation": preparation,
    }


def route_activity(page, jobs, cleared_at=None):
    activity = {"cleared_at": cleared_at, "hidden": {}}
    removals = []

    def hide(route):
        identifier = route.request.post_data_json["job_id"]
        removals.append(identifier)
        chosen = next(job for job in jobs if job["id"] == identifier)
        request = chosen.get("input_preparation", {}).get("request_id")
        for job in jobs:
            if job["id"] == identifier or (
                request and job.get("input_preparation", {}).get("request_id") == request
            ):
                activity["hidden"][job["id"]] = job["updated"]
        route.fulfill(json=activity)

    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.route("**/api/activity", lambda route: route.fulfill(json=activity))
    page.route("**/api/activity/hide", hide)
    return removals


def overlaps(first, second):
    return not (
        first["x"] + first["width"] <= second["x"]
        or second["x"] + second["width"] <= first["x"]
        or first["y"] + first["height"] <= second["y"]
        or second["y"] + second["height"] <= first["y"]
    )


def test_source_preparation_card_layout_and_removal(page, local_app):
    single = intent("1" * 32, "Interview with a long synthetic file name.wav", "d" * 32)
    removals = route_activity(page, [single])
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job")
    expect(card).to_have_count(1)
    expect(card).to_contain_text("Runtime preparation needs your confirmation.")
    provider = card.locator("p.field-note", has_text="For ")
    message = card.locator("p.field-note", has_text="preparation consent")
    badge = card.locator(".job-footer > .badge")
    prepare = card.locator('.job-footer [data-input-action="prepare"]')
    remove = card.get_by_role("button", name="Remove from Activity", exact=True)
    expect(prepare).to_have_text("Prepare and continue")
    expect(badge).to_have_text("terminal")
    page.screenshot(path="test-results/activity-source-preparation-layout.png")
    boxes = {
        name: locator.bounding_box()
        for name, locator in (
            ("provider", provider),
            ("summary", card.locator("p", has_text="needs your confirmation")),
            ("message", message),
            ("badge", badge),
            ("prepare", prepare),
            ("remove", remove),
            ("title", card.locator("h3")),
        )
    }
    # Every line stacks in reading order: no second column and no overlap.
    for control in ("badge", "prepare", "remove"):
        for text in ("provider", "summary", "message", "title"):
            assert not overlaps(boxes[control], boxes[text]), (control, text, boxes)
    assert boxes["provider"]["y"] + boxes["provider"]["height"] <= boxes["summary"]["y"]
    assert boxes["summary"]["y"] + boxes["summary"]["height"] <= boxes["message"]["y"]
    assert boxes["message"]["y"] + boxes["message"]["height"] <= boxes["badge"]["y"]
    # The provider line spans the card instead of forming a cramped second column.
    assert abs(boxes["provider"]["width"] - boxes["title"]["width"]) < 2

    remove.click()
    expect(card).to_have_count(0)
    expect(page.locator("#jobs .empty-jobs")).to_have_text(
        "Finished jobs removed. New jobs will appear here."
    )
    assert removals == [single["id"]]
    page.reload()
    page.locator("#activity-open").click()
    expect(page.locator("#jobs .job")).to_have_count(0)
    # A later update of the same job shows it again.
    single["updated"] = "2026-01-02T00:00:00+00:00"
    page.evaluate("async () => await pollJobs()")
    expect(card).to_have_count(1)


def test_request_files_render_as_one_card_and_remove_together(page, local_app):
    request = "e" * 32
    files = [intent(f"{index:032x}", f"file-{index:02d}.wav", request) for index in range(9)]
    files += [
        intent(f"{index:032x}", f"file-{index:02d}.wav", request, error="cancelled")
        for index in range(9, 11)
    ]
    files.append(intent(f"{11:032x}", "file-11.wav", request, error="source_invalid"))
    files[-1]["updated"] = "2026-01-01T00:00:05+00:00"
    held = [intent(f"a{index:031x}", f"held-{index}.wav", "b" * 32) for index in range(3)]
    held[1]["input_preparation"]["native_group_resolved"] = False
    obligation = {
        "id": "c" * 32,
        "recording_id": "d" * 32,
        "kind": "transcribe",
        "state": "completed",
        "progress": 1,
        "message": "Transcript saved",
        "recording_name": "cleanup.wav",
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:00+00:00",
        "ucloud_cleanup_state": "pending",
    }
    jobs = [*files, *held, obligation]
    removals = route_activity(page, jobs, cleared_at="2026-06-01T00:00:00+00:00")
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    retained = page.locator("#jobs [data-retained-activity]")
    expect(retained.locator(":scope > summary")).to_have_text("3 earlier jobs need attention")
    retained.locator(":scope > summary").click()

    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(3)
    group = page.locator(f'#jobs [data-input-request="intent-{request}"]')
    expect(group).to_have_count(1)
    expect(group.locator("h3")).to_have_text("12 files in one request")
    expect(group).to_contain_text("Runtime preparation needs your confirmation.")
    counts = {
        key: group.locator(f'[data-input-count="{key}"]')
        for key in ("total", "needs_confirmation", "cancelled", "failed")
    }
    expect(counts["total"]).to_have_text("12")
    expect(counts["needs_confirmation"]).to_have_text("9")
    expect(counts["cancelled"]).to_have_text("2")
    expect(counts["failed"]).to_have_text("1")
    expect(group.locator('[data-input-count="waiting"]')).to_have_count(0)
    # One Prepare action for the request; per-file Retry stays in the list.
    expect(group.locator('[data-input-action="prepare"]')).to_have_count(1)
    expect(group.locator('.job-footer > [data-input-action="prepare"]')).to_have_text(
        "Prepare and continue"
    )
    files_list = group.locator("[data-batch-members]")
    expect(files_list.locator("summary")).to_have_text("Per-file outcomes (12)")
    files_list.locator("summary").click()
    items = files_list.locator("li")
    expect(items).to_have_count(12)
    expect(items.locator("a")).to_have_count(12)
    expect(items.first).to_contain_text(CONSENT)
    expect(
        items.filter(has_text="file-11.wav").locator('[data-input-action="retry"]')
    ).to_be_visible()
    page.evaluate("async () => await pollJobs()")
    expect(files_list).to_have_attribute("open", "")
    page.screenshot(path="test-results/activity-request-group.png")

    # A member that still owes its selection decision keeps the whole group.
    held_group = page.locator('#jobs [data-input-request="intent-' + "b" * 32 + '"]')
    expect(held_group.locator('[data-input-count="total"]')).to_have_text("3")
    expect(held_group.get_by_role("button", name="Remove from Activity")).to_have_count(0)
    expect(
        cards.filter(has_text="Transcript saved").get_by_role("button", name="Remove from Activity")
    ).to_have_count(0)

    with page.expect_response("**/api/activity/hide"):
        group.get_by_role("button", name="Remove from Activity", exact=True).click()
    expect(group).to_have_count(0)
    expect(retained.locator(":scope > summary")).to_have_text("2 earlier jobs need attention")
    assert removals == [files[0]["id"]]
    page.reload()
    page.locator("#activity-open").click()
    expect(retained.locator(":scope > summary")).to_have_text("2 earlier jobs need attention")
    expect(page.locator(f'#jobs [data-input-request="intent-{request}"]')).to_have_count(0)


def test_refused_removal_is_shown_in_the_card_and_late_polls_do_not_resurrect(page, local_app):
    first = intent("1" * 32, "refused.wav", "d" * 32)
    second = intent("2" * 32, "removed.wav", "e" * 32)
    jobs = [first, second]
    route_activity(page, jobs)
    page.route(
        "**/api/activity/hide",
        lambda route: (
            route.fulfill(status=409, json={"detail": "This job still has UCloud work to resolve."})
            if route.request.post_data_json["job_id"] == first["id"]
            else route.fallback()
        ),
    )
    stale = copy.deepcopy(jobs)
    stale[1]["updated"] = "2025-12-31T00:00:00+00:00"
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    refused = page.locator("#jobs .job", has_text="refused.wav")
    refused.get_by_role("button", name="Remove from Activity").click()
    expect(refused.locator("[data-activity-remove-error]")).to_have_text(
        "This job still has UCloud work to resolve."
    )
    expect(refused.get_by_role("button", name="Remove from Activity")).to_be_enabled()

    # A slow poll answered after the removal renders nothing older over it.
    held = []
    page.unroute("**/api/jobs")
    page.route("**/api/jobs", lambda route: held.append(route))
    page.evaluate("() => { window.latePoll = pollJobs(); }")
    while not held:
        page.wait_for_timeout(20)
    page.unroute("**/api/jobs")
    page.route("**/api/jobs", lambda route: route.fulfill(json=jobs))
    page.locator("#jobs .job", has_text="removed.wav").get_by_role(
        "button", name="Remove from Activity"
    ).click()
    expect(page.locator("#jobs .job", has_text="removed.wav")).to_have_count(0)
    # Without the newest-render guard this older snapshot would show the card again.
    for route in held:
        route.fulfill(json=stale)
    page.evaluate("async () => await window.latePoll")
    expect(page.locator("#jobs .job", has_text="removed.wav")).to_have_count(0)
    expect(page.locator("#jobs .job", has_text="refused.wav")).to_have_count(1)


def silent_wav():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    return stream.getvalue()


def test_files_added_together_are_one_media_preparation_card(page, local_app):
    store = local_app.server.config.app.state.store
    page.goto(local_app.url)
    create_project(page, "Synthetic upload batch")
    data = silent_wav()
    page.locator("#media-files").set_input_files(
        [
            {"name": f"take-{index}.wav", "mimeType": "audio/wav", "buffer": data}
            for index in range(3)
        ]
    )
    if page.locator("#activity").is_hidden():
        page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    card = page.locator("#jobs .job[data-upload-group]")
    expect(card.locator('[data-upload-count="total"]')).to_have_text("3", timeout=20000)
    expect(cards).to_have_count(1)
    expect(card.locator("h3")).to_have_text("Media preparation — 3 files")
    expect(card.locator('[data-upload-count="done"]')).to_have_text("3", timeout=30000)
    expect(card).to_contain_text("All files are ready for playback.")
    expect(card.locator(".job-footer > .badge")).to_have_text("completed")
    expect(card.locator("progress")).to_have_attribute("value", "1")
    files = card.locator("[data-batch-members]")
    expect(files.locator("summary")).to_have_text("Per-file outcomes (3)")
    files.locator("summary").click()
    expect(files.locator("li")).to_have_count(3)
    for index in range(3):
        expect(files.locator("li a", has_text=f"take-{index}")).to_have_count(1)
    page.screenshot(path="test-results/activity-upload-group.png")
    batches = {
        json.loads(row["params"]).get("upload_batch")
        for row in store.all("SELECT params FROM jobs WHERE kind='media'")
    }
    assert len(batches) == 1 and len(next(iter(batches))) == 32

    with page.expect_response("**/api/activity/hide") as response:
        card.get_by_role("button", name="Remove from Activity", exact=True).click()
    assert response.value.ok
    expect(cards).to_have_count(0)
    # A single file keeps its own ordinary card without an upload batch.
    page.locator("#media-files").set_input_files(
        {"name": "single.wav", "mimeType": "audio/wav", "buffer": data}
    )
    expect(cards).to_have_count(1, timeout=20000)
    expect(page.locator("#jobs .job[data-upload-group]")).to_have_count(0)
    expect(cards.locator("h3")).to_have_text("single")
    assert store.one("SELECT COUNT(*) AS n FROM jobs WHERE kind='media'")["n"] == 4
