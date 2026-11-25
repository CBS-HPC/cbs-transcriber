"""Word timing in review and Activity (ADR 0191); synthetic libraries and stubbed alignment."""

import json
import threading
import uuid
import wave
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_passage_review import save_status, status

from cbs_transcriber import enhancement, media, passage_review
from cbs_transcriber.store import now
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def timed(text, start, end):
    tokens = text.split(" ")
    step = (end - start) / len(tokens)
    return {
        "text": text,
        "start": start,
        "end": end,
        "words": [
            {"word": token, "start": start + i * step, "end": start + (i + 0.8) * step}
            for i, token in enumerate(tokens)
        ],
    }


def show(page):
    return page.get_by_role("combobox", name="Show passages", exact=True)


def visible_ids(page):
    return page.locator("#transcript > .passage:not([hidden])").evaluate_all(
        "nodes => nodes.map(node => node.dataset.segment)"
    )


def summary(page):
    return page.locator("[data-review-summary]")


def test_missing_timing_is_found_by_show_and_next_to_review(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic missing timing")
    rid = store.create_recording(project["id"], "timing.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                timed("Alpha timed words.", 0, 2),
                {"text": "Beta untimed words.", "start": 2, "end": 4},
                timed("Gamma timed words.", 4, 6),
                {"text": "Delta untimed words.", "start": 6, "end": 8},
            ]
        ),
        {},
        "Synthetic words",
        {"capability": "words"},
        auto_first=True,
    )["id"]
    save_status(store, rid, result, "0", "checked")
    speakers = store.create_recording(project["id"], "speakers.wav")["id"]
    store.add_result(
        speakers,
        normalize_segments([{"text": "Speakers only, no timing.", "start": 0, "end": 2}]),
        {},
        "Speakers only",
        {"capability": "speakers"},
        auto_first=True,
    )
    writes = []
    page.on(
        "request",
        lambda request: (
            writes.append(request.url) if request.method in {"POST", "PATCH", "PUT"} else None
        ),
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(summary(page)).to_have_text(
        "Manual review · Checked 1 of 4 · Needs review 2 (2 for word timing)"
    )
    reason = page.locator("#passage-1 [data-review-reason]")
    expect(reason).to_have_text("word timing missing")
    assert "Set automatically" in reason.get_attribute("title")
    expect(status(page, 2)).to_have_value("missing_timing")
    expect(page.locator("#passage-2 [data-review-reason]")).to_have_count(0)
    show(page).select_option("needs_review")
    expect(page.locator("#transcript > .passage:not([hidden])")).to_have_count(2)
    assert visible_ids(page) == ["1", "3"]
    expect(show(page).locator('option[value="needs_review"]')).to_have_text("Needs review (2)")
    following = page.get_by_role("button", name="Next to review", exact=True)
    for expected in ("1", "3", "1"):
        following.click()
        expect(page.locator("#transcript .review-focus")).to_have_attribute(
            "data-segment", expected
        )
    assert not writes, "Showing or visiting automatic marks must not write anything."
    # The researcher's own mark replaces the automatic one and is stored as a row.
    status(page, 2).select_option("checked")
    expect(page.locator("#passage-1 [data-review-reason]")).to_have_count(0)
    expect(summary(page)).to_have_text(
        "Manual review · Checked 2 of 4 · Needs review 1 (1 for word timing)"
    )
    row = next(
        p for p in passage_review.read(store, rid, result)["passages"] if p["segment_id"] == "1"
    )
    assert (row["status"], row["automatic"], row["review_revision"]) == ("checked", None, 1)
    # A result never meant to have word timing is not flagged.
    page.goto(local_app.url + f"/#/recordings/{speakers}")
    expect(summary(page)).to_have_text("Manual review · Checked 0 of 1 · Needs review 0")
    expect(status(page, 1)).to_have_value("not_checked")
    expect(page.locator("[data-review-reason]")).to_have_count(0)


def synthetic_wav(arguments):
    duration = float(arguments[arguments.index("-t") + 1])
    with wave.open(str(Path(arguments[-1])), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * int(duration * 16000))


def test_added_timing_is_marked_counted_and_kept_in_activity(page, local_app, monkeypatch):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic added timing")
    rid = store.create_recording(project["id"], "added.wav")["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    store.directory(rid).mkdir(parents=True, exist_ok=True)
    (store.directory(rid) / "audio.mp3").write_bytes(b"")
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": "God morgen.", "start": 0, "end": 4},
                {"text": "Tak for nu.", "start": 4, "end": 8},
                {"text": "Farvel igen.", "start": 8, "end": 12},
            ]
        ),
        {},
        "Synthetic words",
        {"capability": "words"},
        auto_first=True,
    )["id"]
    # Checked on the untimed wording: it stays Checked until timing changes the passage.
    save_status(store, rid, result, "1", "checked")
    release = threading.Event()

    def align(folder, options, token, cancel, progress, **kw):
        assert release.wait(20), "The test never released the synthetic aligner."
        passages = json.loads((folder / "request.json").read_text(encoding="utf-8"))["passages"]
        enhancement.write_json(
            folder / "alignment.json",
            {
                "passages": [
                    {
                        "id": p["id"],
                        "words": [
                            {"word": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "score": 0.9}
                            for i, w in enumerate(p["text"].split())
                        ],
                    }
                    for p in passages
                ]
            },
        )

    monkeypatch.setattr(media, "ffmpeg", lambda args, cancel: synthetic_wav(args))
    monkeypatch.setattr(enhancement, "run_worker", align)
    monkeypatch.setattr(enhancement, "check_runtime", lambda *a, **kw: {"ready": True})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    bulk = page.locator("#timing-warning [data-run-repair]")
    expect(bulk).to_have_text("Repair all 3 incomplete passages")
    expect(status(page, 2)).to_have_value("checked")
    expect(summary(page)).to_have_text(
        "Manual review · Checked 1 of 3 · Needs review 2 (2 for word timing)"
    )
    page.locator("#passage-1 [data-run-repair]").click()
    # The passage being timed leaves the count at once (B512).
    expect(bulk).to_have_text("Repair all 2 incomplete passages")
    assert "1 more is already being timed" in bulk.get_attribute("title")
    expect(page.locator("#passage-1 [data-run-repair]")).to_be_disabled()
    release.set()
    expect(page.locator("#toast")).to_contain_text("Word timing added", timeout=20000)
    expect(bulk).to_have_text("Repair all 2 incomplete passages")
    assert "already being timed" not in bulk.get_attribute("title")
    # Timing added: Needs review again although it was Checked, with the reason shown.
    expect(status(page, 2)).to_have_value("needs_review")
    expect(page.locator("#passage-1 [data-review-reason]")).to_have_text("timing added")
    expect(summary(page)).to_have_text(
        "Manual review · Checked 0 of 3 · Needs review 3 (3 for word timing)"
    )
    assert store.one("SELECT status,revision FROM passage_reviews WHERE segment_id='1'") == {
        "status": "checked",
        "revision": 1,
    }
    page.locator("#activity-open").click()
    card = page.locator("#jobs .job").filter(has=page.locator("[data-timing-outcome]"))
    expect(card).to_have_count(1)
    expect(card.locator("[data-timing-outcome='added']")).to_have_text(
        "Word timing added to passage 2."
    )
    # The worker's own outcome survives completion instead of a bare "Complete" (B511).
    expect(card).to_contain_text("Complete word timing for 1 of 1 passages.")
    with page.expect_response("**/api/activity/clear") as response:
        page.locator("#activity-clear").click()
    assert response.value.ok
    expect(page.locator("#jobs [data-timing-outcome]")).to_have_count(0)


def finished_timing(store, rid, result, state, message, params, segment="0"):
    identifier = uuid.uuid4().hex
    stamp = now()
    store.execute(
        "INSERT INTO jobs(id,recording_id,kind,state,progress,message,params,created,updated) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            identifier,
            rid,
            "enhance",
            state,
            1,
            message,
            json.dumps(
                {
                    "realign": True,
                    "segment_id": segment,
                    "source_snapshot": {"id": result, "segments": [], "speakers": {}},
                    "enhancement": {"execution": "local"},
                    **params,
                }
            ),
            stamp,
            stamp,
        ),
    )
    return identifier


def test_activity_names_each_word_timing_outcome(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic timing outcomes")
    rid = store.create_recording(project["id"], "outcomes.wav")["id"]
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Ingen tider.", "start": 0, "end": 2}]),
        {},
        "Synthetic words",
        {"capability": "words"},
        auto_first=True,
    )["id"]
    output = {"realign_output": {"passages": {"0": {"words": [], "word_timing": False}}}}
    jobs = {
        "ready": finished_timing(store, rid, result, "completed", "Ready message", output),
        "stale": finished_timing(
            store, rid, result, "completed", "Stale message", output | {"realign_applied": "stale"}
        ),
        "none": finished_timing(
            store, rid, result, "completed", "Word timing could not be found in this audio.", {}
        ),
        "legacy": finished_timing(store, rid, result, "completed", "Complete", {}),
        "failed": finished_timing(store, rid, result, "failed", "Local file operation failed.", {}),
    }
    page.goto(local_app.url)
    page.locator("#activity-open").click()
    cards = page.locator("#jobs .job")
    expect(cards).to_have_count(5)

    def outcome(kind, text):
        expect(
            page.locator(f"#jobs [data-timing-outcome='{kind}']").filter(has_text=text)
        ).to_have_count(1)

    outcome("ready", "Ready, added when you next open the recording.")
    outcome("stale", "Not added: the passage changed.")
    outcome("none", "Not added: word timing could not be found in this audio.")
    outcome("none", "Not added: no word timing was found in the audio.")
    failed = cards.filter(has_text="Local file operation failed.")
    expect(failed.locator("[data-timing-outcome]")).to_have_count(0)
    expect(failed.locator(".badge.failed")).to_have_text("failed")
    assert len(jobs) == 5
