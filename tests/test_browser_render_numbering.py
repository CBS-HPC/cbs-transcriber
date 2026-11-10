"""Filtered passages keep their original accessible positions and structural boundaries."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_filtered_passages_keep_original_positions(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic passage positions")
    recording = store.create_recording(project["id"], "synthetic.wav")
    store.add_result(
        recording["id"],
        normalize_segments(
            [
                {"text": "Alpha", "start": 0, "end": 1, "speaker": "A"},
                {"text": "Beta", "start": 1, "end": 2, "speaker": "B"},
                {"text": "Gamma", "start": 2, "end": 3, "speaker": "C"},
            ]
        ),
        {},
        "Synthetic",
        {},
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    search = page.locator("#transcript-search")
    for text, position in (("Alpha", 1), ("Beta", 2), ("Gamma", 3)):
        search.fill(text)
        expect(page.locator("#transcript .passage")).to_have_count(1)
        expect(
            page.get_by_role("button", name=f"Change speaker for passage {position}")
        ).to_be_visible()
        expect(page.get_by_role("button", name=f"Replay {position}")).to_be_visible()
        expect(page.get_by_role("button", name=f"Delete passage {position}")).to_be_visible()
        passage = page.locator("#transcript .passage")
        up = passage.locator('[data-action="merge-up"]')
        down = passage.locator('[data-action="merge-down"]')
        if position == 1:
            expect(up).to_be_disabled()
        else:
            expect(up).to_be_enabled()
        if position == 3:
            expect(down).to_be_disabled()
        else:
            expect(down).to_be_enabled()
