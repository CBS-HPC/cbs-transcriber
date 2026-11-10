"""The agreed empty library uses the existing brand and project action."""

import pytest
from playwright.sync_api import expect
from test_browser import create_project
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_empty_library_brand_keeps_project_creation(local_app, page):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(local_app.url)
    brand = page.locator("#welcome")
    expect(brand).to_be_visible()
    expect(brand.get_by_role("heading", name="Transcriber", exact=True)).to_be_visible()
    expect(page.locator("#library-view > .page-heading")).to_be_hidden()
    expect(brand.get_by_role("button")).to_have_count(0)
    image = brand.get_by_alt_text("Copenhagen Business School")
    expect(image).to_have_attribute("src", "/static/brand/cbs-logo-vertical.svg")
    assert image.evaluate("node => node.complete && node.naturalWidth > 0")
    for width in (1440, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        assert page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
        )
    create_project(page, "Synthetic project")
    expect(brand).to_be_hidden()
    expect(page.locator("#library-view > .page-heading")).to_be_visible()
    expect(page.locator("#project-title")).to_have_text("Synthetic project")
    assert not errors
