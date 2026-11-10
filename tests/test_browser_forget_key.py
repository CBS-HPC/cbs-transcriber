"""Forget saved key/token sits right under its key box, right-aligned, on every tab."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def under(page, box, button):
    expect(page.locator(button)).to_be_visible()
    field = page.locator(box).bounding_box()
    action = page.locator(button).bounding_box()
    assert 0 <= action["y"] - (field["y"] + field["height"]) <= 12
    assert abs((action["x"] + action["width"]) - (field["x"] + field["width"])) < 2


def test_forget_buttons_follow_their_key_boxes(page, local_app):
    local_app.server.config.app.state.credentials.set(
        "huggingface:https://huggingface.co", "hf_synthetic", False
    )
    page.goto(local_app.url)
    page.locator("#settings-open").click()
    for tab in ("OpenAI API", "UCloud"):
        page.get_by_role("tab", name=tab, exact=True).click()
        # Not tucked into Advanced settings: directly below the key the user pastes.
        assert page.locator("#forget-key").evaluate("e => !e.closest('#provider-advanced')")
        under(page, "#api-key", "#forget-key")
    page.get_by_role("tab", name="Hugging Face", exact=True).click()
    under(page, "#hf-settings-token", "#hf-settings-forget")
