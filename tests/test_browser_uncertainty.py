"""Uncertainty display: relative passage flags, word underlines, review navigation."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def rows():
    values = [-0.1, -0.2, -0.15, -0.9, -0.12, -0.18, -0.11, -0.4, -0.16, -0.13]
    return [
        {
            "text": f"Passage {index} words.",
            "start": index,
            "end": index + 0.9,
            "avg_logprob": value,
            "words": [
                {"word": "Passage", "start": index, "end": index + 0.3, "score": 0.9},
                {"word": str(index), "start": index + 0.3, "end": index + 0.5, "score": 0.9},
                {
                    "word": "words.",
                    "start": index + 0.5,
                    "end": index + 0.9,
                    "score": 0.05 if index == 2 else 0.95,
                },
            ],
        }
        for index, value in enumerate(values)
    ]


def test_lowest_confidence_passages_and_words_are_marked_until_edited(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Uncertainty")
    recording = store.create_recording(project["id"], "a.wav")
    store.execute("UPDATE recordings SET status='ready',duration=12 WHERE id=?", (recording["id"],))
    store.add_result(
        recording["id"], normalize_segments(rows()), {}, "WhisperX", {}, auto_first=True
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#uncertainty-controls")).to_be_visible()
    expect(page.locator("#show-uncertainty")).to_be_checked()
    expect(page.locator(".passage.review")).to_have_count(1)
    expect(page.locator("#passage-3 .review-flag")).to_have_text("Review suggested")
    expect(page.locator("#passage-3 .review-flag")).to_have_attribute(
        "title", __import__("re").compile("decoding confidence.*not a measure of correctness")
    )
    weak = page.locator("#passage-2 .uncertain-word")
    expect(weak).to_have_count(1)
    expect(weak).to_have_text("words.")
    expect(weak).to_have_attribute("title", __import__("re").compile("Low alignment fit"))
    page.locator("#uncertainty-share").select_option("20")
    expect(page.locator(".passage.review")).to_have_count(2)
    # Next to review follows manual Not checked/Needs review, independently of model flags.
    focus = __import__("re").compile("review-focus")
    for expected in ("#passage-0", "#passage-1", "#passage-2"):
        page.locator("#next-uncertain").click()
        expect(page.locator(expected)).to_have_class(focus)
    page.locator("#uncertainty-share").select_option("10")
    expect(page.locator(".passage.review")).to_have_count(1)
    page.locator("#show-uncertainty").uncheck()
    expect(page.locator(".passage.review")).to_have_count(0)
    expect(page.locator(".uncertain-word")).to_have_count(0)
    page.locator("#next-uncertain").click()
    expect(page.locator("#passage-3")).to_have_class(focus)
    expect(page.locator("#next-uncertain")).to_be_enabled()
    page.locator("#show-uncertainty").check()
    # An unambiguous word correction removes this passage's model signals; others remain.
    page.locator("#passage-3 [data-action='edit']").click()
    page.locator("#passage-3 textarea").fill("Corrected 3 words.")
    page.locator("#passage-3 [data-action='save-edit']").click()
    expect(page.locator("#passage-3 .passage-text")).to_have_text("Corrected 3 words.")
    expect(page.locator("#passage-3 .review-flag")).to_have_count(0)
    expect(page.locator("#uncertainty-note")).to_contain_text("1 edited passage")
    expect(page.locator("#passage-2 .uncertain-word")).to_have_count(1)
    page.screenshot(path="test-results/uncertainty-review.png")
