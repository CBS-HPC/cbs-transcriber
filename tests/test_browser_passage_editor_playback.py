"""The inline editor keeps a draft while replaying the recorded passage."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_review_layout import seed_review

pytestmark = pytest.mark.browser


def test_inline_replay_keeps_draft_caret_and_passage_boundary(page, local_app):
    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    passage = page.locator("#passage-1")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    draft = passage.locator("textarea")
    expect(passage.locator(".editor-playback-preview")).to_be_hidden()
    draft.fill("Synthetic edited passage")
    draft.evaluate("e => e.setSelectionRange(4, 4)")
    expect(draft).to_be_focused()
    assert draft.evaluate("e => e.spellcheck") is False

    replay = passage.locator('[data-action="editor-replay"]')
    expect(replay).to_have_text("▷ Replay passage")
    replay.click()
    expect(draft).to_be_focused()
    assert draft.evaluate("e => e.selectionStart") == 4
    expect(draft).to_have_value("Synthetic edited passage")
    expect(replay).to_have_text("Ⅱ Pause passage")
    assert page.locator("#audio").evaluate("e => !e.paused && e.currentTime >= 100")

    replay.click()
    assert page.locator("#audio").evaluate("e => e.paused")
    expect(draft).to_be_focused()
    draft.press("Shift+Tab")
    expect(replay).to_be_focused()
    page.locator("#audio").evaluate("e => e.playbackRate = 4")
    replay.press("Enter")
    expect(replay).to_be_focused()
    page.wait_for_function("() => document.querySelector('#audio').paused", timeout=10000)
    assert page.locator("#audio").evaluate("e => e.currentTime") < 103
    expect(replay).to_have_text("▷ Replay passage")
    expect(draft).to_have_value("Synthetic edited passage")


def test_word_save_while_listening_keeps_playback_and_working_role(page, local_app):
    store = local_app.server.config.app.state.store
    _, recording_id, result, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    editor = passage.locator(".passage-editor")
    editor.locator('[data-action="editor-replay"]').click()
    page.wait_for_function("() => !document.querySelector('#audio').paused")
    editor.locator("[data-editor-mode]").select_option("timing")
    editor.locator('[data-action="edit-timed-word"]').click()
    editor.locator("[data-timing-text]").fill("corrected")
    editor.locator('[data-action="save-timed-word"]').click()
    expect(page.locator("#save-status")).to_have_text("All changes saved")
    expect(editor.locator("[data-timing-text]")).to_have_value("corrected")
    assert page.locator("#audio").evaluate("e => !e.paused")
    recording = store.recording(recording_id)
    assert (
        store.result(recording_id, result["id"])["segments"][0]["words"][0]["word"] == "corrected"
    )
    assert recording["result_roles"]["working"]["version_id"] == recording["active_version"]["id"]
    page.locator("#audio").evaluate("e => { e.currentTime = 99.8; e.playbackRate = 4; }")
    page.wait_for_function("() => document.querySelector('#audio').paused", timeout=10000)
    assert page.locator("#audio").evaluate("e => e.currentTime") < 101
    expect(editor.locator("[data-timing-text]")).to_have_value("corrected")


def test_untimed_editor_replay_names_the_full_recording(page, local_app):
    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    expect(page.locator("#passage-1")).to_be_visible()
    page.evaluate("""() => {
      state.recording.segments[1].start = null;
      state.recording.segments[1].end = null;
      state.recording.replay_windows = {};
      renderTranscript();
    }""")
    passage = page.locator("#passage-1")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    replay = passage.locator('[data-action="editor-replay"]')
    expect(replay).to_have_text("▷ Play full recording")
    expect(replay).to_have_attribute(
        "title", "This passage has no start/end timestamps; plays the full recording."
    )


def test_long_passage_opens_without_a_word_per_option(page, local_app):
    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    passage = page.locator("#passage-0")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    editor = passage.locator(".passage-editor")
    expect(editor.locator("[data-timing-word]")).to_have_count(1)
    expect(editor.locator("[data-timing-word] option")).to_have_count(0)
    editor.locator("[data-editor-mode]").select_option("timing")
    words = editor.locator("[data-timing-word]")
    expect(words.locator("option")).to_have_count(200)
    words.select_option("19")
    expect(words).to_have_value("19")
    expect(words.locator('option[value="19"]')).to_have_text("19 – word18")
    expect(editor.locator("[data-timing-start]")).to_have_value("9")


def test_realign_choice_is_frozen_for_this_save(page, local_app):
    from cbs_transcriber import enhancement

    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    enhancement.save_realign(store, False)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    passage = page.locator("#passage-1")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    setting = passage.locator("[data-editor-realign]")
    expect(setting).not_to_be_checked()
    # Another client changes the library default after this editor was opened.
    enhancement.save_realign(store, True)
    requests = []
    page.on(
        "request",
        lambda request: (
            requests.append(request.post_data_json)
            if request.method == "PATCH" and "/segments/" in request.url
            else None
        ),
    )
    passage.locator("textarea").fill("Keep this explicit choice")
    passage.locator('[data-action="save-edit"]').click()
    expect(passage.locator(".passage-text")).to_have_text("Keep this explicit choice")
    assert requests and "realign" not in requests[-1]
    assert not store.all(
        "SELECT id FROM jobs WHERE recording_id=? AND kind='enhance'", (recording_id,)
    )


@pytest.mark.parametrize("choice", [False, True])
def test_realign_choice_survives_clean_search_redraw(page, local_app, choice):
    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    passage = page.locator("#passage-1")
    passage.locator('[data-action="edit"]').click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()
    passage.locator("[data-editor-realign]").set_checked(choice)
    page.evaluate(
        "window.beforeSearchEditor = document.querySelector('#passage-1 .passage-editor')"
    )
    page.locator("#transcript-search").fill("Later passage")
    page.wait_for_function(
        "() => window.beforeSearchEditor !== document.querySelector('#passage-1 .passage-editor')"
    )
    assert passage.locator("[data-editor-realign]").is_checked() is choice
