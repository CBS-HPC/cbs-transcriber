"""Opening a merge must preserve drafts and guard stale results without needless redraws."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed(page, local_app, *, wait_for_hints=True):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic merge performance")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Alpha"}, {"text": "Beta"}, {"text": "Gamma"}]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#transcript .passage")).to_have_count(3)
    if wait_for_hints:
        page.wait_for_function(
            "() => hintCache.has(`${state.recording.active_transcript_id}:"
            "${state.recording.revision}`)"
        )
    return store, recording["id"]


def test_merge_open_keeps_nodes_without_edit_and_rejects_stale_result(page, local_app):
    store, identifier = seed(page, local_app)
    page.evaluate("window.passageBeforeMerge = document.querySelector('#passage-0')")
    page.locator('#passage-0 [data-action="merge-down"]').click()
    expect(page.locator("#passage-merge-dialog")).to_be_visible()
    assert page.evaluate("window.passageBeforeMerge === document.querySelector('#passage-0')")

    newer = store.recording(identifier)
    newer["segments"][2]["text"] = "Newer text"
    store.save_transcript(identifier, newer["segments"], newer["speakers"], newer["revision"])
    page.evaluate(
        """async id => {
          const response = await fetch(`/api/recordings/${id}`);
          state.recording = await response.json();
        }""",
        identifier,
    )
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    expect(page.locator("#passage-merge-error")).to_contain_text("transcript changed")
    assert len(store.recording(identifier)["segments"]) == 3
    assert store.recording(identifier)["segments"][2]["text"] == "Newer text"


def test_merge_preserves_cancelled_draft_and_clears_confirmed_draft(page, local_app):
    store, identifier = seed(page, local_app)
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("Draft text")
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator('#passage-1 [data-action="merge-down"]').click()
    expect(page.locator("#passage-0 textarea")).to_have_value("Draft text")
    expect(page.locator("#passage-merge-dialog")).not_to_be_visible()

    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-1 [data-action="merge-down"]').click()
    expect(page.locator("#passage-merge-dialog")).to_be_visible()
    expect(page.locator("#transcript textarea")).to_have_count(0)
    assert [item["text"] for item in store.recording(identifier)["segments"]] == [
        "Alpha",
        "Beta",
        "Gamma",
    ]


def test_merge_open_restarts_hints_if_initial_request_is_pending(page, local_app):
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      let first = true;
      const hints = {origin: 'synthetic', passages: {'0': {
        avg_logprob: -2, no_speech_prob: 0.9
      }}, edited_passages: 0};
      const response = () => new Response(JSON.stringify(hints), {
        headers: {'Content-Type': 'application/json'}
      });
      window.fetch = (url, options) => {
        if (!String(url).endsWith('/hints')) return original(url, options);
        if (first) {
          first = false;
          window.oldHintsHeld = true;
          return new Promise(resolve => { window.releaseOldHints = () => resolve(response()); });
        }
        return Promise.resolve(response());
      };
    })()""")
    seed(page, local_app, wait_for_hints=False)
    page.wait_for_function("() => window.oldHintsHeld === true")
    page.locator('#passage-0 [data-action="merge-down"]').click()
    expect(page.locator("#passage-merge-dialog")).to_be_visible()
    expect(page.locator("#show-uncertainty")).to_be_checked()
    expect(page.locator("#transcript .review-flag")).to_have_count(1)
    page.evaluate("window.releaseOldHints()")
    expect(page.locator("#transcript .review-flag")).to_have_count(1)
