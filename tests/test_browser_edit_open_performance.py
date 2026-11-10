"""Opening passage edits should avoid rebuilding unrelated transcript nodes."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_merge_open_performance import seed
from test_browser_review_layout import seed_review

pytestmark = pytest.mark.browser


def test_edit_open_and_cancel_keep_active_playback_highlights(page, local_app):
    store = local_app.server.config.app.state.store
    _, recording_id, _, _ = seed_review(store)
    page.goto(local_app.url + f"/#/recordings/{recording_id}")
    expect(page.locator("#passage-0")).to_be_visible()
    page.wait_for_function("() => document.querySelector('#audio').readyState >= 2")
    page.locator("#audio").evaluate(
        "async audio => { audio.currentTime = 2.1; audio.playbackRate = 0.1; await audio.play(); }"
    )
    expect(page.locator("#passage-0 [data-word='4']")).to_have_class("word current")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.get_by_role("button", name="Leave unassigned").click()
    expect(page.locator("#passage-0 textarea")).to_be_focused()
    assert not page.locator("#audio").evaluate("audio => audio.paused")
    page.locator("#passage-0").get_by_role("button", name="Cancel", exact=True).click()
    page.locator("#audio").evaluate("audio => audio.currentTime = 2.1")
    expect(page.locator("#passage-0 [data-word='4']")).to_have_class("word current")
    assert not page.locator("#audio").evaluate("audio => audio.paused")
    page.locator("#audio").evaluate("audio => audio.pause()")


def test_edit_opens_in_place_and_existing_draft_uses_safe_fallback(page, local_app):
    seed(page, local_app)
    page.evaluate("""() => {
      window.originalPassage = document.querySelector('#passage-0');
      window.otherPassage = document.querySelector('#passage-2');
    }""")
    page.locator('#passage-0 [data-action="edit"]').click()
    input_ = page.locator("#passage-0 textarea")
    expect(input_).to_be_focused()
    expect(input_).to_have_value("Alpha")
    assert page.evaluate("window.originalPassage === document.querySelector('#passage-0')")
    assert page.evaluate("window.otherPassage === document.querySelector('#passage-2')")

    input_.fill("Draft text")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('#passage-1 [data-action="edit"]').click()
    expect(input_).to_have_value("Draft text")
    expect(page.locator("#passage-1 textarea")).to_have_count(0)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-1 [data-action="edit"]').click()
    expect(page.locator("#passage-1 textarea")).to_be_focused()
    expect(page.locator("#transcript textarea")).to_have_count(1)
    expect(page.locator("#passage-0 .passage-text")).to_contain_text("Alpha")


def test_delayed_hints_do_not_replace_a_focused_pristine_edit(page, local_app):
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      const pending = [];
      const hints = {origin: 'synthetic', passages: {'0': {
        avg_logprob: -2, no_speech_prob: 0.9
      }}, edited_passages: 0};
      window.fetch = (url, options) => {
        if (!String(url).endsWith('/hints')) return original(url, options);
        window.oldHintsHeld = true;
        return new Promise(resolve => {
          pending.push(resolve);
          window.releaseOldHints = () => pending.splice(0).forEach(done => done(
            new Response(JSON.stringify(hints), {
              headers: {'Content-Type': 'application/json'}
            })
          ));
        });
      };
    })()""")
    seed(page, local_app, wait_for_hints=False)
    page.wait_for_function("() => window.oldHintsHeld === true")
    page.locator('#passage-0 [data-action="edit"]').click()
    input_ = page.locator("#passage-0 textarea")
    expect(input_).to_be_focused()
    input_.evaluate("(input) => input.setSelectionRange(2, 2)")
    page.evaluate("window.releaseOldHints()")
    page.wait_for_function("""() => hintCache.has(
      `${state.recording.active_transcript_id}:${state.recording.revision}`
    )""")
    expect(input_).to_be_focused()
    expect(input_).to_have_value("Alpha")
    assert input_.evaluate("(input) => input.selectionStart") == 2
    page.locator("#passage-0").get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#passage-0 .review-flag")).to_have_count(1)
