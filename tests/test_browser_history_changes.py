"""History change list and Revert this change (ADR 0181), against a real local server."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_change_undo import edit_text, open_recording

pytestmark = pytest.mark.browser


def open_history(page):
    page.locator("#history-open").click()
    expect(page.locator("#history-dialog")).to_be_visible()
    expect(page.locator("#history-versions .history-version").first).to_be_visible()


def version_row(page, sequence):
    return page.locator("#history-versions .history-version").filter(
        has_text=f"Version {sequence} ·"
    )


def merge_first_two(page):
    page.locator('#passage-0 [data-action="merge-down"]').click()
    page.locator("#passage-merge-form").get_by_role(
        "button", name="Merge passages", exact=True
    ).click()
    expect(page.locator("#transcript .passage")).to_have_count(2)


def test_rows_name_changes_and_show_word_level_differences(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic change list")
    edit_text(page, 1, "Second passage, carefully corrected")
    merge_first_two(page)
    page.locator("#undo-last").click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    open_history(page)
    expect(version_row(page, 4)).to_contain_text("Undo last change")
    merged = version_row(page, 3)
    expect(merged).to_contain_text("Merge")
    expect(merged).to_contain_text("undone by version 4")
    expect(merged).to_have_class("history-version button history-undone")
    edited = version_row(page, 2)
    expect(edited).to_contain_text("Text edit · passage 2")
    edited.click()
    changes = page.locator("#history-changes")
    expect(changes).to_contain_text("1 passage changed compared with version 1.")
    # Whitespace tokens: "passage" became "passage," plus two new words.
    expect(changes.locator("ins")).to_have_text(["passage, carefully corrected"])
    expect(changes.locator("del")).to_have_text(["passage"])
    expect(changes).to_contain_text("Passage 2 · Anna · text changed")
    expect(page.locator("#history-revert")).to_be_hidden()
    version_row(page, 4).click()
    expect(changes).to_contain_text("Undid version 3.")
    expect(changes).to_contain_text("1 passage changed, 1 added")
    version_row(page, 1).click()
    expect(changes).to_contain_text("First saved version of this result: 3 passages.")
    expect(page.locator("#history-text")).to_contain_text("First passage")


def test_revert_an_older_merge_keeps_later_edits_and_hides_passage_buttons(page, local_app):
    store, rid, source = open_recording(page, local_app, "Synthetic revert merge")
    merge_first_two(page)
    # The per-passage control stays beside History (user, 20 November; amends ADR 0181).
    expect(page.locator('[data-action="undo-merge"]')).to_have_count(1)
    edit_text(page, 2, "Third passage, edited later")  # DOM IDs follow passage IDs.
    open_history(page)
    merged = version_row(page, 2)
    expect(merged).to_contain_text("can be reverted")
    merged.click()
    revert = page.locator("#history-revert")
    expect(revert).to_be_visible()
    expect(page.locator("#history-revert-note")).to_be_visible()
    revert.click()
    expect(page.locator("#history-dialog")).to_be_hidden()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    expect(page.locator("#passage-2 .passage-text")).to_have_text("Third passage, edited later")
    segments = store.recording(rid)["segments"]
    assert segments[:2] == source[:2]
    assert segments[2]["text"] == "Third passage, edited later"
    newest = store.result_versions(rid, store.recording(rid)["active_transcript_id"])[0]
    assert newest["action"] == "passage_merge_undo"


def test_revert_an_older_deletion_from_history(page, local_app):
    store, rid, source = open_recording(page, local_app, "Synthetic revert delete")
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('#passage-1 [data-action="delete"]').click()
    expect(page.locator("#transcript .passage")).to_have_count(2)
    expect(page.locator("#deleted-passages")).to_be_visible()
    edit_text(page, 0, "First passage, edited later")
    open_history(page)
    deleted = version_row(page, 2)
    expect(deleted).to_contain_text("Delete")
    expect(deleted).to_contain_text("can be reverted")
    deleted.click()
    expect(page.locator("#history-changes")).to_contain_text("Passage 2 · Anna · removed")
    page.locator("#history-revert").click()
    expect(page.locator("#transcript .passage")).to_have_count(3)
    segments = store.recording(rid)["segments"]
    assert segments[1] == source[1]
    assert segments[0]["text"] == "First passage, edited later"
    expect(page.locator("#deleted-passages")).to_be_hidden()


def test_revert_waits_for_a_draft_and_offers_only_proven_changes(page, local_app):
    store, rid, _ = open_recording(page, local_app, "Synthetic revert draft")
    merge_first_two(page)
    page.locator('#passage-2 [data-action="edit"]').click()
    page.locator("#passage-2 textarea").fill("Unsaved draft")
    open_history(page)
    version_row(page, 2).click()
    revert = page.locator("#history-revert")
    expect(revert).to_be_visible()
    expect(revert).to_be_disabled()
    expect(revert).to_have_attribute("title", "Save or cancel your passage correction first.")
    expect(page.locator("#passage-2 textarea")).to_have_value("Unsaved draft")
    # The creation version has nothing to revert.
    version_row(page, 1).click()
    expect(revert).to_be_hidden()
    assert len(store.recording(rid)["segments"]) == 2
