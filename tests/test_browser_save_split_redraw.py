"""Real saved edits paint once; optional hints never replace the transcript again."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def seed(page, local_app, count=6):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic redraw")
    recording = store.create_recording(project["id"], "synthetic.wav")
    words = ["Alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {
                    "text": " ".join(words),
                    "start": index * 6,
                    "end": index * 6 + 5.8,
                    "speaker": "A",
                    "avg_logprob": -2,
                    "no_speech_prob": 0.9,
                    "words": [
                        {
                            "word": word,
                            "start": index * 6 + offset,
                            "end": index * 6 + offset + 0.8,
                            "probability": 0.2 if offset == 0 else 0.99,
                        }
                        for offset, word in enumerate(words)
                    ],
                }
                for index in range(count)
            ]
        ),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#transcript > .passage")).to_have_count(count)
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    page.evaluate("""() => {
      const render = renderTranscript;
      window.fullRenders = 0;
      renderTranscript = (...args) => { window.fullRenders++; return render(...args); };
      const fetch = window.fetch.bind(window);
      window.hintRequests = 0;
      window.editWrites = [];
      window.hintMode = 'normal';
      window.heldHints = [];
      window.fetch = async (url, options = {}) => {
        if (['POST', 'PATCH', 'PUT', 'DELETE'].includes(options.method))
          window.editWrites.push([String(url), options.method]);
        if (!String(url).endsWith('/hints')) return fetch(url, options);
        window.hintRequests++;
        if (window.hintMode === 'fail')
          return new Response(JSON.stringify({detail: 'Synthetic hints unavailable'}), {
            status: 503, headers: {'Content-Type': 'application/json'}
          });
        const response = await fetch(url, options);
        if (window.hintMode !== 'hold') return response;
        return new Promise(resolve => window.heldHints.push(replacement => resolve(
          replacement ? new Response(JSON.stringify(replacement === 'fail'
            ? {detail: 'Synthetic late hints failure'} : replacement), {
              status: replacement === 'fail' ? 503 : 200,
              headers: {'Content-Type': 'application/json'}
            }) : response
        )));
      };
    }""")
    return store, recording["id"], result["id"]


def release_hints(page):
    page.wait_for_function("() => window.heldHints.length > 0")
    page.evaluate("""() => {
      window.hintMode = 'normal';
      const held = window.heldHints.splice(0);
      held.forEach(release => release());
    }""")
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )


def test_long_real_save_and_split_do_not_repaint_again_when_changed_hints_arrive(page, local_app):
    store, identifier, result_id = seed(page, local_app, count=600)
    expect(page.locator("#passage-0 .review-flag")).to_have_count(1)
    review = page.locator("#passage-10 [data-review-status]")
    expect(review).to_be_enabled()
    review.select_option("checked")
    expect(page.locator("#manual-review-progress")).to_contain_text("Checked 1 of 600")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("Alpha beta gamma delta epsilon zeta corrected")
    before = store.recording(identifier)
    page.evaluate("window.fullRenders = 0; window.hintMode = 'hold'; window.editWrites = []")
    page.locator('#passage-0 [data-action="save-edit"]').click()
    page.wait_for_function(
        "rev => state.recording.revision === rev + 1 && !state.editing",
        arg=before["revision"],
        timeout=60000,
    )
    page.evaluate("window.afterSave = document.getElementById('passage-10')")
    release_hints(page)
    assert page.evaluate("window.fullRenders") == 1
    assert page.evaluate("window.afterSave === document.getElementById('passage-10')")
    expect(page.locator("#passage-0 .review-flag")).to_have_count(0)
    expect(page.locator("#passage-10 .review-flag")).to_have_count(1)
    expect(review).to_have_value("checked")
    expect(page.locator("#timing-coverage")).to_contain_text("599 complete, 1 partial")
    current = store.recording(identifier)
    assert current["segments"][0]["text"].endswith(" corrected")
    assert current["segments"][10] == before["segments"][10]
    assert current["result_roles"]["working"]["version_id"] == current["active_version"]["id"]

    page.locator('#passage-1 [data-action="edit"]').click()
    page.locator("#passage-1 textarea").evaluate("""input => {
      input.setSelectionRange(11, 11);
      input.dispatchEvent(new Event('select', {bubbles: true}));
    }""")
    page.evaluate("window.fullRenders = 0; window.hintMode = 'hold'")
    page.locator('#passage-1 [data-action="split"]').click()
    expect(page.locator("#transcript > .passage")).to_have_count(601)
    page.evaluate("window.afterSplit = document.getElementById('passage-10')")
    release_hints(page)
    assert page.evaluate("window.fullRenders") == 1
    assert page.evaluate("window.afterSplit === document.getElementById('passage-10')")
    expect(review).to_have_value("checked")
    expect(page.locator("#manual-review-progress")).to_contain_text("Checked 1 of 601")
    expect(page.locator("#timing-coverage")).to_contain_text("600 complete, 1 partial")
    after = store.recording(identifier)
    assert (
        after["segments"][1]["text"] + after["segments"][2]["text"]
        == current["segments"][1]["text"]
    )
    assert after["segments"][11] == current["segments"][10]
    assert after["active_transcript_id"] == result_id
    assert page.evaluate("window.editWrites.map(item => item[1])") == ["PATCH", "POST"]


def test_same_view_hint_loads_coalesce_and_failed_loads_can_retry_without_writes(page, local_app):
    seed(page, local_app)
    page.evaluate("""() => {
      hintCache.delete(`${state.recording.active_transcript_id}:${state.recording.revision}`);
      window.hintMode = 'hold';
      window.hintLoads = Promise.allSettled([loadHints(), loadHints(), loadHints()]);
    }""")
    page.wait_for_function("() => window.heldHints.length > 0")
    assert page.evaluate("window.hintRequests") == 1
    release_hints(page)
    page.evaluate("window.hintLoads")
    assert page.evaluate("window.fullRenders") == 0

    page.evaluate("""async () => {
      hintCache.delete(`${state.recording.active_transcript_id}:${state.recording.revision}`);
      window.hintMode = 'fail';
      await Promise.allSettled([loadHints(), loadHints()]);
    }""")
    assert page.evaluate("window.hintRequests") == 2
    assert not page.evaluate(
        "hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    page.evaluate("window.hintMode = 'normal'; loadHints()")
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    assert page.evaluate("window.hintRequests") == 3
    assert page.evaluate("window.editWrites") == []
    assert page.evaluate("window.fullRenders") == 0


def test_hint_failure_does_not_delay_or_undo_a_real_save(page, local_app):
    store, identifier, _ = seed(page, local_app)
    page.locator('#passage-0 [data-action="edit"]').click()
    text = "Alpha beta gamma delta epsilon zeta corrected"
    page.locator("#passage-0 textarea").fill(text)
    page.evaluate("window.fullRenders = 0; window.hintMode = 'fail'")
    page.locator('#passage-0 [data-action="save-edit"]').click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text(text, timeout=60000)
    assert store.recording(identifier)["segments"][0]["text"] == text
    assert page.evaluate("!state.dirty && !state.editing && !passageTextSaving")
    assert page.evaluate("window.fullRenders") == 1
    page.evaluate("window.hintMode = 'normal'; loadHints()")
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    assert page.evaluate("window.fullRenders") == 1
    assert page.evaluate("window.editWrites.map(item => item[1])") == ["PATCH"]


def test_old_hint_failure_cannot_remove_a_newer_contexts_inflight_request(page, local_app):
    seed(page, local_app)
    page.evaluate("""() => {
      hintCache.delete(`${state.recording.active_transcript_id}:${state.recording.revision}`);
      window.hintMode = 'hold';
      window.firstLoad = loadHints().catch(() => {});
    }""")
    page.wait_for_function("() => window.heldHints.length === 1")
    page.evaluate("() => { clearEdit(); window.secondLoad = loadHints(); }")
    page.wait_for_function("() => window.heldHints.length === 2")
    page.evaluate("window.heldHints[0]('fail'); window.firstLoad")
    page.evaluate("() => { loadHints(); }")
    assert page.evaluate("window.hintRequests") == 2
    page.evaluate("window.heldHints[1](); window.secondLoad")
    assert page.evaluate("window.fullRenders") == 0
    assert page.evaluate("window.editWrites") == []


def test_late_hints_preserve_an_open_draft_and_apply_on_cancel_without_rebuild(page, local_app):
    seed(page, local_app)
    page.evaluate("""() => {
      hintCache.delete(`${state.recording.active_transcript_id}:${state.recording.revision}`);
      window.hintMode = 'hold';
      window.beforeDraftHints = loadHints();
    }""")
    page.wait_for_function("() => window.heldHints.length === 1")
    page.locator('#passage-0 [data-action="edit"]').click()
    page.locator("#passage-0 textarea").fill("Uncommitted draft text")
    page.locator("#passage-0 textarea").evaluate("""input => {
      window.draftInput = input;
      input.setSelectionRange(3, 8);
    }""")
    page.wait_for_function("() => window.heldHints.length === 2")
    page.evaluate("""() => {
      window.hintMode = 'normal';
      window.heldHints[1]({origin: 'new', passages: {}, edited_passages: 6});
    }""")
    page.wait_for_function("() => currentHints()?.origin === 'new'")
    page.evaluate("""async () => {
      window.heldHints[0]({origin: 'old', passages: {'0': {no_speech_prob: 1, avg_logprob: -3}}});
      await window.beforeDraftHints;
    }""")
    expect(page.locator("#passage-0 textarea")).to_have_value("Uncommitted draft text")
    assert page.evaluate("window.draftInput === document.querySelector('#passage-0 textarea')")
    assert page.locator("#passage-0 textarea").evaluate(
        "input => [input.selectionStart, input.selectionEnd]"
    ) == [3, 8]
    assert page.evaluate("currentHints().origin") == "new"
    page.locator('#passage-0 [data-action="cancel-edit"]:visible').click()
    expect(page.locator("#transcript .review-flag")).to_have_count(0)
    assert page.evaluate("window.fullRenders") == 0
    assert page.evaluate("window.editWrites") == []
