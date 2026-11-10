"""Collections on the project page (ADR 0060) and Back up library (ADR 0061)."""

from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_collections_filter_move_settings_and_delete_without_losing_recordings(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Waves")
    names = ["alpha.wav", "beta.wav", "gamma.wav"]
    recordings = [store.create_recording(project["id"], name) for name in names]
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    chips = page.locator("#collection-chips")
    assert chips.evaluate(
        "element => element.parentElement === "
        "document.querySelector('#transcribe-selected').parentElement"
    )
    expect(chips.locator("[data-collection='']")).to_contain_text("All 3")
    # A new collection is created from its chip and shown at once (empty).
    page.once("dialog", lambda dialog: dialog.accept("Wave 1"))
    page.locator("#collection-new").click()
    expect(chips.get_by_role("button", name="Wave 1 0")).to_have_attribute("aria-pressed", "true")
    expect(page.locator(".recording-card")).to_have_count(0)
    # Collection Settings take Project Settings' place while a collection is shown.
    expect(page.locator("#collection-settings-open")).to_be_visible()
    expect(page.locator("#project-settings-open")).to_be_hidden()
    # Move two recordings into it from All.
    chips.locator("[data-collection='']").click()
    expect(page.locator(".recording-card")).to_have_count(3)
    for recording in recordings[:2]:
        page.locator(f"[data-select='{recording['id']}']").check()
    [wave] = store.collections(project["id"])
    page.locator("#bulk-collection").select_option(wave["id"])
    expect(page.locator("#toast")).to_contain_text("Moved 2 recording(s)")
    expect(chips.get_by_role("button", name="Wave 1 2")).to_be_visible()
    expect(chips.get_by_role("button", name="Unsorted 1")).to_be_visible()
    chips.get_by_role("button", name="Wave 1 2").click()
    expect(page.locator(".recording-card")).to_have_count(2)
    # Select all selects only what is shown.
    page.locator("#select-all").click()
    expect(page.locator("[data-select]:checked")).to_have_count(2)
    page.locator("#deselect-all").click()
    page.screenshot(path="test-results/collections.png")
    # Rename and annotate in Collection Settings.
    page.locator("#collection-settings-open").click()
    page.locator("#collection-name").fill("Wave one")
    page.locator("#collection-notes").fill("Spring interviews.")
    page.locator("#collection-settings-form button[type=submit]").click()
    expect(page.locator("#collection-status")).to_have_text("Saved.")
    assert store.collection(wave["id"])["notes"] == "Spring interviews."
    # Deleting the collection keeps its recordings, now unsorted.
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#collection-delete").click()
    expect(page.locator("#collection-settings-dialog")).to_be_hidden()
    expect(page.locator(".recording-card")).to_have_count(3)
    expect(page.locator("#project-settings-open")).to_be_visible()
    assert all(store.recording(r["id"])["collection_id"] is None for r in recordings)


def test_existing_database_only_copy_remains_available_in_advanced_restore(page, local_app):
    from cbs_transcriber.backup import create_backup

    store = local_app.server.config.app.state.store
    store.create_project("Backed up")
    saved = create_backup(store)
    page.goto(local_app.url)
    page.locator("#library-open").click()
    page.get_by_text("Restore a backup", exact=True).click()
    page.get_by_text("Older database copies (no recordings)").click()
    expect(page.locator("#library-backups .library-item")).to_have_count(1)
    expect(page.locator("#legacy-backups")).to_contain_text("cannot restore missing media")
    expect(page.locator("#library-backup")).to_have_count(0)
    [folder] = (store.root / "backups").glob("library-*")
    assert (Path(folder) / "manifest.json").exists()
    assert str(folder) == saved["folder"]
