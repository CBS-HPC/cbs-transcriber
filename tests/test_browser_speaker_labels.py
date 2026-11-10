"""Speaker names shown for the reviewed result only, with Transcriber-style defaults."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_reviewed_result_shows_only_its_speakers_with_default_names(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Speakers")
    recording = store.create_recording(project["id"], "Interview.wav")
    store.execute("UPDATE recordings SET status='ready',duration=6 WHERE id=?", (recording["id"],))
    store.add_result(
        recording["id"],
        normalize_segments([{"text": "Other version.", "start": 0, "end": 2, "speaker": "C"}]),
        {"C": "Somebody else"},
        "Other",
        {},
    )
    reviewed = store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Hej.", "start": 0, "end": 2, "speaker": "ENH_ab12_1"},
                {"text": "Ja.", "start": 2, "end": 4, "speaker": "ENH_ab12_2"},
                {"text": "Godt.", "start": 4, "end": 6, "speaker": "SPEAKER_00"},
            ]
        ),
        # Earlier enhancements saved generated names and an unused "multiple" label.
        {
            "ENH_ab12_1": "Speaker 1",
            "ENH_ab12_2": "Speaker 2",
            "ENH_ab12_MULTIPLE": "Multiple speakers (passage)",
            "SPEAKER_00": "Interviewer",
        },
        "Reviewed",
        {},
        activate=True,
        revision=store.recording(recording["id"])["revision"],
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#result-select")).to_have_value(reviewed["id"])
    expect(page.locator(".passage .speaker-badge")).to_have_text(
        ["Speaker_01", "Speaker_02", "Interviewer"]
    )
    expect(page.locator("#speakers .speaker-entry")).to_have_text(
        ["Speaker_01↗", "Speaker_02↗", "Interviewer↗"]
    )
    assert store.recording(recording["id"])["result_roles"]["working"] is None
    page.get_by_role("button", name="Change speaker for passage 1", exact=True).click()
    choice = page.get_by_role("dialog", name="Which transcript should be Working?")
    expect(choice).to_be_visible()
    choice.get_by_role("button", name="Leave unassigned", exact=True).click()
    options = page.locator("#passage-speaker-select option")
    expect(options).to_have_text(["Speaker_01", "Speaker_02", "Interviewer", "＋ Add speaker…"])
    page.locator("#speaker-dialog").get_by_role("button", name="Cancel").click()
    assert store.recording(recording["id"])["result_roles"]["working"] is None
    assert store.result(recording["id"], reviewed["id"])["speakers"]["ENH_ab12_1"] == "Speaker 1"


@pytest.mark.parametrize("width", [1440, 390])
def test_speaker_colors_match_and_labels_fit(page, local_app, width):
    store = local_app.server.config.app.state.store
    project = store.create_project("Speaker colors")
    recording = store.create_recording(project["id"], "Interview.wav")
    store.execute("UPDATE recordings SET status='ready',duration=10 WHERE id=?", (recording["id"],))
    speakers = ["SPEAKER_00", "ENH_ab12_MULTIPLE", "SPEAKER_02", "SPEAKER_03", "UNKNOWN"]
    store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": f"Passage {i}", "start": i * 2, "end": i * 2 + 2, "speaker": speaker}
                for i, speaker in enumerate(speakers)
            ]
        ),
        {
            "ENH_ab12_MULTIPLE": "Multiple speakers (passage)",
            "SPEAKER_02": "A very long speaker name that must remain available in full",
        },
        "Reviewed",
        {},
        activate=True,
        revision=store.recording(recording["id"])["revision"],
    )
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    badges = page.locator("#transcript .speaker-badge")
    dots = page.locator("#speakers .speaker-dot")
    expect(badges).to_have_count(5)
    expect(dots).to_have_count(5)
    colors = page.evaluate(
        """() => [
          [...document.querySelectorAll('#transcript .speaker-badge')],
          [...document.querySelectorAll('#speakers .speaker-dot')],
        ].map(items => items.map(item => getComputedStyle(item).backgroundColor))"""
    )
    assert len(colors[0]) == len(colors[1]) == 5
    assert colors[0] == colors[1]
    for i in (0, 1):
        assert badges.nth(i).evaluate("e => e.scrollWidth <= e.clientWidth + 1")
    assert badges.nth(1).get_attribute("title") == "Multiple speakers (passage)"
    assert badges.nth(2).get_attribute("title") == (
        "A very long speaker name that must remain available in full"
    )
