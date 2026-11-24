"""The row controls use the same labels as the recording toolbar."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_change_undo import edit_text, open_recording

pytestmark = pytest.mark.browser


def test_passage_row_steps_through_saved_edits_without_reverting_other_rows(page, local_app):
    open_recording(page, local_app, "Synthetic scoped undo")
    edit_text(page, 0, "First passage, edit one")
    edit_text(page, 1, "Second passage, kept")
    edit_text(page, 0, "First passage, edit two")

    passage = page.locator("#passage-0")
    passage.get_by_role("button", name="Undo / Redo", exact=True).click()
    panel = passage.get_by_role("group", name="Saved edits for this passage")
    undo = panel.get_by_role("button", name="Undo last text edit", exact=True)
    redo = panel.get_by_role("button", name="Redo text edit", exact=True)
    first = passage.locator(".passage-text")
    second = page.locator("#passage-1 .passage-text")

    undo.click()
    expect(first).to_have_text("First passage, edit one")
    undo.click()
    expect(first).to_have_text("First passage")
    expect(second).to_have_text("Second passage, kept")
    expect(panel.get_by_role("button", name="Undo", exact=True)).to_be_disabled()
    redo.click()
    expect(first).to_have_text("First passage, edit one")
    redo.click()
    expect(first).to_have_text("First passage, edit two")
    expect(second).to_have_text("Second passage, kept")
    expect(panel.get_by_role("button", name="Redo", exact=True)).to_be_disabled()
    expect(page.locator("#undo-last")).to_have_text("↶ Undo last passage redo")
