"""Fresh Text saves preserve proved word times without adding or guessing timing."""

import pytest
from playwright.sync_api import expect
from test_browser import LocalApp
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_working_edit_choice import older_working

from cbs_transcriber import enhancement
from cbs_transcriber.transcripts import edit_segment, normalize_segments

pytestmark = pytest.mark.browser


@pytest.fixture(autouse=True)
def ordinary_text_uses_mvp_profile(monkeypatch):
    # The module's ordinary Text cases are MVP checks. The explicit full-app fixture
    # below overrides this only for its opt-in realignment-route check.
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "mvp")


def seed_editor(store, words=("Før", "København."), *, stale=False):
    project = store.create_project("Conservative Text timing")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    source = normalize_segments(
        [
            {
                "text": " ".join(words),
                "start": 0,
                "end": len(words),
                "words": [
                    {"word": word, "start": index, "end": index + 0.8}
                    for index, word in enumerate(words)
                ],
            }
        ]
    )
    if stale:
        source[0] = edit_segment(source[0], "free", "Tidligere tekst")
    result = store.add_result(rid, source, {}, "Synthetic", {}, auto_first=True)
    store.execute("UPDATE recordings SET status='ready',duration=? WHERE id=?", (len(words), rid))
    return rid, result["id"], source[0]


def open_editor(page, app, rid):
    page.goto(app.url + f"/#/recordings/{rid}")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    return passage


def save_text(page, passage, text):
    passage.locator("textarea").fill(text)
    with page.expect_response(
        lambda response: response.url.endswith("/segments/0/edit-v3")
    ) as reply:
        passage.locator('[data-action="save-edit"]').click()
    return reply.value


def test_text_replacement_retains_intervals_and_inserted_word_stays_untimed(page, local_app):
    store = local_app.server.config.app.state.store
    rid, result_id, source = seed_editor(store)
    # A legacy standing setting must not cause an ordinary MVP Text save to align.
    enhancement.save_realign(store, True)
    passage = open_editor(page, local_app, rid)
    response = save_text(page, passage, "Efter København.")
    assert response.status == 200, response.text()
    assert "realign" not in response.request.post_data_json
    assert set(response.request.post_data_json) <= {
        "revision",
        "mode",
        "text",
        "working_choice",
        "reason",
    }
    expect(passage.locator(".passage-text")).to_have_text("Efter København.")
    replaced = store.result(rid, result_id)["segments"][0]
    assert [(word["start"], word["end"]) for word in replaced["words"]] == [
        (word["start"], word["end"]) for word in source["words"]
    ]
    expect(passage.locator(".word")).to_have_count(2)
    passage.locator('[data-action="edit"]').click()
    response = save_text(page, passage, "Efter nye København.")
    assert response.status == 200, response.text()
    expect(passage.locator(".passage-text")).to_have_text("Efter nye København.")
    saved = store.result(rid, result_id)["segments"][0]
    assert [(word["word"], word["start"], word["end"]) for word in saved["words"]] == [
        ("Efter", 0, 0.8),
        ("nye", None, None),
        ("København.", 1, 1.8),
    ]
    assert saved["original_words"] == source["original_words"]
    assert saved["words_current"] is True and saved["word_timing"] is False
    expect(passage.locator(".word")).to_have_count(2)
    passage.locator('[data-action="edit"]').click()
    passage.locator("[data-editor-mode]").select_option("timing")
    passage.locator("[data-timing-word]").select_option("2")
    expect(passage.locator("[data-timing-start]")).to_have_value("")
    expect(passage.locator("[data-timing-end]")).to_have_value("")
    assert store.all("SELECT id FROM jobs") == []


def test_ambiguous_word_deletion_saves_with_only_uncertain_words_untimed(page, local_app):
    store = local_app.server.config.app.state.store
    rid, result_id, source = seed_editor(store, ("ja", "ja", "tak"))
    before = store.recording(rid)
    versions = store.result_versions(rid, result_id)
    passage = open_editor(page, local_app, rid)
    draft = passage.locator("textarea")
    draft.fill("ja tak")
    with page.expect_response(
        lambda response: response.url.endswith("/segments/0/edit-v3")
    ) as reply:
        passage.locator('[data-action="save-edit"]').click()
    assert reply.value.status == 200, reply.value.text()
    expect(passage.locator("textarea")).to_have_count(0)
    expect(passage.locator(".passage-text")).to_have_text("ja tak")
    saved = store.result(rid, result_id)["segments"][0]
    assert saved["words"][0]["start"] is None and saved["words"][0]["end"] is None
    assert (saved["words"][1]["start"], saved["words"][1]["end"]) == (
        source["words"][2]["start"],
        source["words"][2]["end"],
    )
    assert saved["original_words"] == source["original_words"]
    assert store.recording(rid)["revision"] == before["revision"] + 1
    assert len(store.result_versions(rid, result_id)) == len(versions) + 1
    warning = reply.value.json()["timing_retention_warning"]
    expect(page.locator("#save-status")).to_contain_text(warning)
    assert "realign" not in reply.value.request.post_data_json
    assert store.all("SELECT id FROM jobs") == []


def test_repeated_end_word_append_keeps_every_saved_prefix_interval(page, local_app):
    store = local_app.server.config.app.state.store
    rid, result_id, source = seed_editor(store, ("ja", "tak", "tak"))
    passage = open_editor(page, local_app, rid)
    response = save_text(page, passage, "ja tak tak tak")
    assert response.status == 200, response.text()
    expect(passage.locator(".passage-text")).to_have_text("ja tak tak tak")
    saved = store.result(rid, result_id)["segments"][0]
    assert saved["words"][:-1] == source["words"]
    assert saved["words"][-1]["start"] is None and saved["words"][-1]["end"] is None
    assert saved["original_words"] == source["original_words"]
    assert store.all("SELECT id FROM jobs") == []


def test_already_stale_timing_stays_untimed_and_warning_is_visible(page, local_app):
    store = local_app.server.config.app.state.store
    rid, result_id, source = seed_editor(store, stale=True)
    passage = open_editor(page, local_app, rid)
    response = save_text(page, passage, "Senere tekst")
    assert response.status == 200, response.text()
    warning = response.json()["timing_retention_warning"]
    assert isinstance(warning, str) and warning
    expect(passage.locator(".passage-text")).to_have_text("Senere tekst")
    expect(page.locator("#save-status")).to_contain_text(warning)
    expect(page.locator("#toast")).to_contain_text(warning)
    saved = store.result(rid, result_id)["segments"][0]
    assert all(word["start"] is None and word["end"] is None for word in saved["words"])
    assert saved["original_words"] == source["original_words"]
    expect(passage.locator(".word")).to_have_count(0)
    assert store.all("SELECT id FROM jobs") == []


def test_fresh_text_keeps_the_explicit_working_choice(page, local_app):
    store, rid, result_id, old = older_working(local_app)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Keep current Working"
    ).click()
    response = save_text(page, passage, "Keep this new review wording.")
    assert response.status == 200, response.text()
    assert response.request.post_data_json.get("working_choice") is not None
    expect(passage.locator(".passage-text")).to_have_text("Keep this new review wording.")
    recording = store.recording(rid)
    assert recording["result_roles"]["working"]["version_id"] == old
    assert recording["active_version"]["id"] != old
    assert store.result(rid, result_id)["segments"][0]["text"] == "Keep this new review wording."


def test_fresh_text_waits_for_pending_recording_language_without_losing_text(page, local_app):
    store = local_app.server.config.app.state.store
    rid, _, _ = seed_editor(store)
    passage = open_editor(page, local_app, rid)
    draft = passage.locator("textarea")
    draft.fill("Efter København.")
    page.evaluate("id => pendingRecordingLanguages.add(id)", rid)
    requests = []
    page.on(
        "request",
        lambda request: requests.append(request.url) if request.method == "PATCH" else None,
    )
    passage.locator('[data-action="save-edit"]').click()
    expect(page.locator("#toast")).to_contain_text("language settings")
    expect(draft).to_be_enabled()
    expect(draft).to_have_value("Efter København.")
    assert not requests
    page.evaluate("id => pendingRecordingLanguages.delete(id)", rid)
    response = save_text(page, passage, "Efter København.")
    assert response.status == 200, response.text()


@pytest.fixture
def full_local_app(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "full")
    app = LocalApp(tmp_path_factory.mktemp("fulltext") / "lib")
    app.start()
    yield app
    app.stop()


def test_full_explicit_realign_uses_v1_but_unchecked_text_uses_v3(page, full_local_app):
    store = full_local_app.server.config.app.state.store
    rid, _, _ = seed_editor(store)
    passage = open_editor(page, full_local_app, rid)
    choice = passage.locator("[data-editor-realign]")
    expect(choice).to_be_visible()
    choice.check()
    passage.locator("textarea").fill("Efter København.")
    captured = []

    def refuse_legacy(route):
        captured.append(route.request.post_data_json)
        route.fulfill(status=400, json={"detail": "Synthetic legacy-route receipt"})

    page.route("**/segments/0/edit-v1", refuse_legacy)
    with page.expect_response(lambda response: response.url.endswith("/segments/0/edit-v1")):
        passage.locator('[data-action="save-edit"]').click()
    assert len(captured) == 1 and captured[0]["realign"] is True
    expect(passage.locator("textarea")).to_have_value("Efter København.")
    choice.uncheck()
    response = save_text(page, passage, "Efter København.")
    assert response.status == 200, response.text()
    assert "realign" not in response.request.post_data_json
    expect(passage.locator(".passage-text")).to_have_text("Efter København.")
    assert store.all("SELECT id FROM jobs") == []
