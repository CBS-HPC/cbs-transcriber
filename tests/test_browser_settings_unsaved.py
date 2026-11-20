"""Settings decisions of 19 November: reclick asks before discarding; UCloud Protected box."""

import pytest
from browser_native_support import NATIVE_KEY, restore_native_key, save_ready_native
from browser_provider_support import mock_native_check
from playwright.sync_api import expect
from test_browser import deny_protected
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


@pytest.fixture
def ready(local_app, monkeypatch):
    app = local_app.server.config.app
    mock_native_check(monkeypatch, key=NATIVE_KEY)
    # Reopening a connected UCloud tab runs a read-only Runtime probe; keep it offline.
    monkeypatch.setattr(
        "cbs_transcriber.enhancement_api.runtime_prepared", lambda *_: {"prepared": None}
    )
    # As a new connection saved in Settings: UCloud defaults to allowed for Protected.
    saved = save_ready_native(app.state.store, app.state.credentials, approved_for=["protected"])
    restore_native_key(app)
    return app, saved


def open_settings(page, local_app):
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    expect(page.locator("#settings-dialog")).to_be_visible()
    expect(page.get_by_role("tab", name="UCloud", exact=True)).to_have_attribute(
        "aria-selected", "true"
    )


def test_reselecting_the_open_tab_asks_before_discarding_unsaved_entries(page, local_app, ready):
    open_settings(page, local_app)
    tab = page.get_by_role("tab", name="UCloud", exact=True)
    dialogs = []

    def dismiss(dialog):
        dialogs.append(dialog.message)
        dialog.dismiss()

    page.on("dialog", dismiss)
    # Nothing typed: reselecting reloads (and retries) without asking.
    tab.click()
    expect(page.locator("#api-key")).to_have_value("")
    assert dialogs == []
    page.locator("#api-key").fill("typed-but-not-saved")
    tab.click()
    expect(page.locator("#api-key")).to_have_value("typed-but-not-saved")
    assert dialogs == ["Discard your unsaved changes on this tab and reload it?"]
    page.remove_listener("dialog", dismiss)
    page.once("dialog", lambda dialog: dialog.accept())
    tab.click()
    expect(page.locator("#api-key")).to_have_value("")
    # Hugging Face: a typed token is unsaved; Processing saves on change and never asks.
    hf = page.get_by_role("tab", name="Hugging Face", exact=True)
    hf.click()
    page.locator("#hf-settings-token").fill("hf_typed-not-saved")
    page.once("dialog", lambda dialog: dialog.dismiss())
    hf.click()
    expect(page.locator("#hf-settings-token")).to_have_value("hf_typed-not-saved")
    page.once("dialog", lambda dialog: dialog.accept())
    hf.click()
    expect(page.locator("#hf-settings-token")).to_have_value("")
    processing = page.get_by_role("tab", name="Local Processing", exact=True)
    processing.click()
    expect(page.locator("#processing-panel")).to_be_visible()
    page.on("dialog", dismiss)
    count = len(dialogs)
    processing.click()
    expect(page.locator("#processing-panel")).to_be_visible()
    assert len(dialogs) == count


def test_ucloud_protected_box_is_hidden_while_allowed_and_returns_for_a_denial(
    page, local_app, ready
):
    app, saved = ready
    open_settings(page, local_app)
    box = page.locator('[name="approved-for"][value="protected"]')
    expect(box).to_be_checked()
    expect(box).to_be_hidden()
    expect(page.locator("#provider-policy-summary")).to_have_text(
        "Allowed for Standard and Protected projects."
    )
    page.get_by_role("button", name="Close settings").click()
    deny_protected(app.state.store, saved["id"])
    page.reload()
    page.locator("#settings-open").click()
    page.locator("#provider-advanced summary").click()
    expect(box).to_be_visible()
    expect(box).not_to_be_checked()
    # Allowing it again saves the approval; the box hides on the next opening.
    box.check()
    page.get_by_role("button", name="Save settings").click()
    expect(page.locator("#settings-dialog")).not_to_be_visible()
    stored = app.state.store.one("SELECT value FROM providers WHERE id=?", (saved["id"],))
    assert '"protected"' in stored["value"]
    page.locator("#settings-open").click()
    expect(box).to_be_hidden()
