"""The Working button reflects the exact version, not only its parent result."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import result_roles
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_working_button_tracks_viewed_result_and_exact_version(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic Working button")
    rid = store.create_recording(project["id"], "synthetic.wav")["id"]
    first = store.add_result(
        rid, normalize_segments([{"text": "First"}]), {}, "First", {}, auto_first=True
    )
    first_version = store.result_versions(rid, first["id"])[0]["id"]
    second = store.add_result(rid, normalize_segments([{"text": "Second"}]), {}, "Second", {})
    page.goto(local_app.url + f"/#/recordings/{rid}")
    button = page.locator("#working-mark-current")
    expect(page.locator("#result-select")).to_have_value(first["id"])
    expect(button).to_be_disabled()
    page.locator("#result-select").select_option(second["id"])
    page.wait_for_function("id => state.recording.active_transcript_id === id", arg=second["id"])
    expect(button).to_be_enabled()
    page.locator("#result-select").select_option(first["id"])
    page.wait_for_function("id => state.recording.active_transcript_id === id", arg=first["id"])
    expect(button).to_be_disabled()

    current = store.recording(rid)
    store.save_transcript(
        rid,
        normalize_segments([{"text": "Newer wording"}]),
        {},
        current["revision"],
        result_id=first["id"],
    )
    current = store.recording(rid)
    preview = result_roles.preview(
        store, rid, first_version, current["revision"], current["result_roles"]["revision"]
    )
    result_roles.apply(
        store,
        rid,
        first_version,
        current["revision"],
        current["result_roles"]["revision"],
        preview["fingerprint"],
    )
    page.reload()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Newer wording")
    expect(button).to_be_enabled()
