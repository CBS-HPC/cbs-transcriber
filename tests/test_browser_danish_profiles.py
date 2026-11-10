"""Fixed Danish candidates stay explicit in Settings; no inference or downloads."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_native_model_profiles import native_settings

from cbs_transcriber import providers

pytestmark = pytest.mark.browser


def test_danish_profiles_are_identified_without_per_model_controls(page, local_app):
    app = local_app.server.config.app
    providers.save_profile(app.state.store, app.state.credentials, "Synthetic", native_settings())
    saved = providers.public_profiles(app.state.store, app.state.credentials)["providers"]
    fixed = [
        p
        for p in saved
        if p["model"] in {"whisperx/edda-turbo", "whisperx/edda-large", "whisperx/roest-v3"}
    ]
    assert len(fixed) == 3
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    page.get_by_role("tab", name="UCloud", exact=True).click()
    expect(page.locator("#settings-dialog h2")).to_have_text("Settings")
    for profile in fixed:
        checkbox = page.locator(f'[data-enabled-model="{profile["model"]}"]')
        # CBS Runtime remains available for explicit opt-in, but is parked by default.
        expect(checkbox).not_to_be_checked()
        expect(checkbox.locator("..")).to_contain_text("(Danish)")
    expect(page.locator("#provider-model-list select")).to_have_count(0)
    assert all(
        providers.public_profile(app.state.store, app.state.credentials, p["id"])["language"]
        == "da"
        for p in fixed
    )
    page.screenshot(path="test-results/danish-profile-settings.png")
