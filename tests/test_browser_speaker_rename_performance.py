"""Speaker rename keeps the currently rendered transcript intact."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_speaker_rename_updates_labels_without_rebuilding_passages(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic rename")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "One synthetic passage", "speaker": "SPEAKER_00"},
                {"text": "Another synthetic passage", "speaker": "SPEAKER_01"},
                {"text": "Last synthetic passage", "speaker": "SPEAKER_00"},
            ]
        ),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#transcript .passage")).to_have_count(3)
    # Let initial hint loading finish before checking whether rename replaces the nodes.
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    page.evaluate("window.savedPassage = document.querySelector('#transcript .passage')")
    page.locator('#speakers [data-speaker="SPEAKER_00"]').click()
    page.locator("#name-input").fill("Renamed speaker")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator('#speakers [data-speaker="SPEAKER_00"]')).to_contain_text("Renamed speaker")
    expect(page.locator("#transcript .speaker-badge")).to_have_text(
        ["Renamed speaker", "Speaker_02", "Renamed speaker"]
    )
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    assert page.evaluate("window.savedPassage === document.querySelector('#transcript .passage')")
    assert (
        store.result(recording["id"], result["id"])["speakers"]["SPEAKER_00"] == "Renamed speaker"
    )
    assert (
        store.one("SELECT COUNT(*) AS n FROM result_versions WHERE result_id=?", (result["id"],))[
            "n"
        ]
        == 2
    )


def test_assigning_speaker_preserves_passages_and_updates_color_order(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic assignment")
    recording = store.create_recording(project["id"], "synthetic.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "One", "speaker": "SPEAKER_00"},
                {"text": "Two", "speaker": "SPEAKER_01"},
                {"text": "Three", "speaker": "SPEAKER_00"},
            ]
        ),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#transcript .passage")).to_have_count(3)
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    page.evaluate("window.savedPassage = document.querySelector('#transcript .passage')")
    page.get_by_role("button", name="Change speaker for passage 1", exact=True).click()
    page.locator("#passage-speaker-select").select_option("SPEAKER_01")
    page.locator("#passage-speaker-save").click()
    expect(page.locator("#transcript .speaker-badge")).to_have_text(
        ["Speaker_02", "Speaker_02", "Speaker_01"]
    )
    page.wait_for_function(
        "() => hintCache.has(`${state.recording.active_transcript_id}:${state.recording.revision}`)"
    )
    assert page.evaluate("window.savedPassage === document.querySelector('#transcript .passage')")
    badges = page.locator("#transcript .speaker-badge")
    dots = page.locator("#speakers .speaker-dot")
    assert badges.nth(0).evaluate("e => getComputedStyle(e).backgroundColor") == dots.nth(
        0
    ).evaluate("e => getComputedStyle(e).backgroundColor")
    assert badges.nth(2).evaluate("e => getComputedStyle(e).backgroundColor") == dots.nth(
        1
    ).evaluate("e => getComputedStyle(e).backgroundColor")
    assert store.result(recording["id"], result["id"])["segments"][0]["speaker"] == "SPEAKER_01"
    assert (
        store.one("SELECT COUNT(*) AS n FROM result_versions WHERE result_id=?", (result["id"],))[
            "n"
        ]
        == 2
    )


def test_speaker_rename_rebuilds_comparison_for_new_revision(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic comparison")
    recording = store.create_recording(project["id"], "synthetic.wav")
    previous = store.add_result(
        recording["id"], normalize_segments([{"text": "Before", "speaker": "A"}]), {}, "First", {}
    )
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "After", "speaker": "A"}]),
        {},
        "Second",
        {},
        activate=True,
        revision=store.recording(recording["id"])["revision"],
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator("#compare-result").select_option(previous["id"])
    page.wait_for_function("() => !!state.comparison")
    page.locator('#speakers [data-speaker="A"]').click()
    choice = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(choice).to_be_visible()
    choice.get_by_role("button", name="Leave unassigned", exact=True).click()
    page.locator("#name-input").fill("New name")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#transcript .speaker-badge")).to_have_text("New name")
    page.wait_for_function("() => alignmentCache?.key === comparisonKey() && !!alignmentCache.rows")
    assert store.recording(recording["id"])["result_roles"]["working"] is None


def test_speaker_rename_closes_an_open_passage_editor(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic open edit")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "One", "speaker": "A"}]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.locator('#transcript [data-action="edit"]').click()
    expect(page.locator("#transcript textarea")).to_be_visible()
    page.locator('#speakers [data-speaker="A"]').click()
    page.locator("#name-input").fill("New name")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#transcript textarea")).to_have_count(0)
    expect(page.locator("#transcript .speaker-badge")).to_have_text("New name")


def test_speaker_rename_loads_current_hints_when_earlier_request_is_pending(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic delayed hints")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "One", "speaker": "A"}]),
        {},
        "Synthetic",
        {},
        auto_first=True,
    )
    page.add_init_script("""(() => {
      const fetchOriginal = window.fetch.bind(window);
      let first = true;
      const hints = {origin: 'synthetic', passages: {'0': {
        avg_logprob: -2, no_speech_prob: 0.9
      }}, edited_passages: 0};
      const response = () => new Response(JSON.stringify(hints), {
        headers: {'Content-Type': 'application/json'}
      });
      window.fetch = (url, options) => {
        if (!String(url).endsWith('/hints')) return fetchOriginal(url, options);
        if (first) {
          first = false;
          window.oldHintsHeld = true;
          return new Promise(resolve => { window.releaseOldHints = () => resolve(response()); });
        }
        return Promise.resolve(response());
      };
    })()""")
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    page.wait_for_function("() => window.oldHintsHeld === true")
    page.locator('#speakers [data-speaker="A"]').click()
    page.locator("#name-input").fill("New name")
    page.locator("#name-form").get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#transcript .speaker-badge")).to_have_text("New name")
    expect(page.locator("#show-uncertainty")).to_be_checked()
    expect(page.locator("#transcript .review-flag")).to_have_count(1)
    page.evaluate("window.releaseOldHints()")
    expect(page.locator("#transcript .review-flag")).to_have_count(1)
