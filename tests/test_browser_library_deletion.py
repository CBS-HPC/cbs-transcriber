"""Browser acceptance of permanent library deletion in the Library dialog (ADR 0144)."""

from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import LocalApp
from test_browser import page as page
from test_library_deletion import build

from cbs_transcriber import libraries, storage
from cbs_transcriber.store import Store

pytestmark = pytest.mark.browser


def test_library_dialog_previews_confirms_and_deletes(page, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(storage, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "legacy_library_root", lambda: tmp_path / "legacy")
    monkeypatch.setattr(libraries, "redirected_roots", lambda: [])
    old, external = build(tmp_path / "old", tmp_path / "Projects")
    busy, _ = build(tmp_path / "busy", tmp_path / "BusyProjects")
    recording = busy.all("SELECT id FROM recordings")[0]["id"]
    busy.add_job(recording, "transcribe", {})
    # A second project folder of the old library that the busy library also uses: kept.
    shared = Path(
        old.create_project("Shared", storage_parent=str(tmp_path / "Shared"))["storage_dir"]
    )
    busy.execute(
        "UPDATE projects SET storage_dir=? WHERE id=(SELECT project_id FROM recordings LIMIT 1)",
        (str(shared),),
    )
    current = Store(tmp_path / "current")
    app = LocalApp(current.root)
    app.start()
    try:
        page.goto(app.url)
        page.locator("#library-open").click()
        dialog = page.locator("#library-dialog")
        panel = dialog.locator("[data-library-delete-panel]")
        expect(dialog.locator(".library-item.current")).to_have_count(1)
        expect(dialog.locator(".library-item.current [data-library-delete]")).to_have_count(0)

        # Unsettled work: the preview explains it and offers no confirmation.
        dialog.locator(".library-item").filter(has_text=str(busy.root)).get_by_role(
            "button", name="Delete permanently"
        ).click()
        expect(panel).to_be_visible()
        blockers = panel.locator("[data-library-delete-blockers]")
        expect(blockers).to_contain_text("cannot be deleted yet")
        expect(blockers).to_contain_text("Nothing has been deleted")
        expect(blockers).to_contain_text("waiting or still running")
        expect(panel.locator("[data-library-delete-confirm]")).to_have_count(0)
        panel.get_by_role("button", name="Close").click()
        expect(panel).to_be_hidden()

        dialog.locator(".library-item").filter(has_text=str(old.root)).get_by_role(
            "button", name="Delete permanently"
        ).click()
        expect(panel).to_contain_text(f"Delete the library “{old.root.name}” permanently?")
        expect(panel).to_contain_text("This cannot be undone.")
        expect(panel).to_contain_text("2 recording(s)")
        expect(panel).to_contain_text("Will be deleted")
        expect(panel).to_contain_text(str(old.root))
        expect(panel).to_contain_text(str(external))
        expect(panel).to_contain_text("Will be kept")
        expect(panel).to_contain_text(str(shared))
        expect(panel).to_contain_text(f"Another library ({busy.root}) also uses this folder")
        # Explanatory notes must not squeeze the confirmation into a vertical column.
        for width in (1440, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            field = panel.locator("label:has([data-library-delete-name])")
            field.scroll_into_view_if_needed()
            panel_box = panel.bounding_box()
            field_box = field.bounding_box()
            label_box = field.locator("span").bounding_box()
            input_box = field.locator("input").bounding_box()
            assert field_box["width"] >= panel_box["width"] * 0.85
            assert input_box["width"] >= panel_box["width"] * 0.85
            assert input_box["y"] >= label_box["y"] + label_box["height"]
            assert input_box["x"] + input_box["width"] <= panel_box["x"] + panel_box["width"]
            for note in panel.locator(":scope > .field-note").all():
                note_box = note.bounding_box()
                assert note_box["y"] + note_box["height"] <= field_box["y"]
            for item in panel.locator("ul > li").all():
                path_box = item.locator(".library-path").bounding_box()
                facts_box = item.locator(".library-facts").bounding_box()
                assert facts_box["y"] >= path_box["y"] + path_box["height"]
            assert panel.evaluate("node => node.scrollWidth <= node.clientWidth + 1")
        page.set_viewport_size({"width": 1440, "height": 1000})
        confirm = panel.get_by_role("button", name="Delete permanently", exact=True)
        expect(confirm).to_be_disabled()
        name = panel.get_by_label("type the folder name")
        name.fill("OLD")
        expect(confirm).to_be_disabled()
        assert old.root.exists() and external.exists()
        name.fill(old.root.name)
        expect(confirm).to_be_enabled()
        confirm.click()
        status = dialog.locator("[data-library-status]")
        expect(status).to_contain_text(f"The library “{old.root.name}” was permanently deleted")
        expect(status).to_contain_text("1 project folder(s) outside it were kept")
        expect(panel).to_be_hidden()
        expect(dialog.locator(".library-item").filter(has_text=str(old.root))).to_have_count(0)
        assert not old.root.exists() and not external.exists()
        assert shared.exists() and busy.root.exists() and current.root.exists()
    finally:
        app.stop()
