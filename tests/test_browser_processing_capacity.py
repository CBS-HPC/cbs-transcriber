"""Capacity saves/reopens use an isolated synthetic library, never an allocation."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_processing_replies import open_processing

from cbs_transcriber import enhancement, runtime_store

pytestmark = pytest.mark.browser


def capacity_requests(page):
    held = []
    page.evaluate("""() => {
      const fetchRequest = window.fetch.bind(window);
      window.capacitySends = [];
      window.fetch = (url, options) => {
        if (url === '/api/runtime-batches/settings')
          window.capacitySends.push(options?.method || 'GET');
        return fetchRequest(url, options);
      };
    }""")

    def capacity(route):
        if route.request.method == "PUT":
            held.append(route)
            page.evaluate("count => window.capacityWrites = count", len(held))
        else:
            route.continue_()

    page.route("**/api/runtime-batches/settings", capacity)
    return held


def commit(route):
    response = route.fetch()
    assert response.status == 200
    route.fulfill(response=response)
    return response.json()


def reopen_ucloud(page):
    page.keyboard.press("Escape")
    page.locator("#settings-open").click()
    page.locator("#tab-ucloud").click()
    page.locator("#provider-advanced > summary").click()
    page.evaluate("() => new Promise(requestAnimationFrame)")


def begin_capacity_save(page, local_app):
    open_processing(page, local_app)
    expect(page.locator("#processing-panel #runtime-max-allocations")).to_have_count(0)
    held = capacity_requests(page)
    page.locator("#tab-ucloud").click()
    page.locator("#provider-advanced > summary").click()
    limit = page.locator("#runtime-max-allocations")
    expect(limit).to_have_value("2")
    expect(limit).to_be_enabled()
    page.evaluate("window.capacitySends = []")
    limit.fill("3")
    limit.press("Enter")
    page.wait_for_function("() => window.capacityWrites === 1")
    assert held[0].request.post_data_json == {"max_allocations": 3}
    return held, limit


def test_invalid_capacity_does_not_block_provider_form(page, local_app):
    open_processing(page, local_app)
    page.locator("#tab-ucloud").click()
    page.locator("#provider-advanced > summary").click()
    limit = page.locator("#runtime-max-allocations")
    expect(limit).to_be_enabled()
    limit.fill("9")
    assert limit.evaluate("input => input.form.id") == "runtime-capacity-form"
    page.locator("#provider-advanced > summary").click()
    page.evaluate("""() => {
      const form = document.querySelector('#settings-form');
      for (const input of form.querySelectorAll('input:invalid, select:invalid'))
        input.disabled = true;
      form.addEventListener('submit', event => {
        window.providerSubmitObserved = true;
        event.preventDefault();
        event.stopImmediatePropagation();
      }, {capture: true, once: true});
    }""")
    page.locator('#settings-form button[type="submit"]').click()
    assert page.evaluate("window.providerSubmitObserved === true")


def test_capacity_reopen_waits_before_reading_an_accepted_save(page, local_app):
    held, limit = begin_capacity_save(page, local_app)
    reopen_ucloud(page)
    # A GET here reads the old2 while the accepted3 is deliberately still uncommitted.
    assert page.evaluate("window.capacitySends.filter(method => method === 'GET').length") == 0
    expect(limit).to_be_disabled()
    assert commit(held[0]) == {"max_allocations": 3}
    expect(limit).to_be_enabled()
    expect(limit).to_have_value("3")
    assert runtime_store.settings(local_app.server.config.app.state.store) == {"max_allocations": 3}


def test_capacity_reopen_cannot_admit_a_write_that_overtakes_the_older_save(page, local_app):
    held, limit = begin_capacity_save(page, local_app)
    reopen_ucloud(page)
    if page.evaluate("window.capacitySends.includes('GET')"):
        # Adversarial baseline: the reopened old read admits4, then its older3 commits last.
        expect(limit).to_be_enabled()
        limit.fill("4")
        limit.press("Enter")
        page.wait_for_function("() => window.capacityWrites === 2")
        commit(held[1])
        commit(held[0])
    else:
        # With the read barrier,3 must settle before this view can accept the later4.
        commit(held[0])
        expect(limit).to_be_enabled()
        expect(limit).to_have_value("3")
        limit.fill("4")
        limit.press("Enter")
        page.wait_for_function("() => window.capacityWrites === 2")
        commit(held[1])
    expect(limit).to_be_enabled()
    assert runtime_store.settings(local_app.server.config.app.state.store) == {"max_allocations": 4}
    expect(limit).to_have_value("4")


@pytest.mark.parametrize("failed", [False, True])
def test_capacity_completion_preserves_other_sections_and_current_error_rollback(
    page, local_app, failed
):
    held, limit = begin_capacity_save(page, local_app)
    store = local_app.server.config.app.state.store
    language = enhancement.local_settings(store).language
    page.locator("#native-storage").fill("/unsaved-ucloud-draft")
    if failed:
        held[0].fulfill(status=503, json={"detail": "Synthetic capacity save failed"})
        expect(limit).to_be_enabled()
        expect(limit).to_have_value("2")
        expected = 2
    else:
        commit(held[0])
        expect(limit).to_be_enabled()
        expect(limit).to_have_value("3")
        expected = 3
    expect(limit).to_be_enabled()
    expect(page.locator("#native-storage")).to_have_value("/unsaved-ucloud-draft")
    assert enhancement.local_settings(store).language == language
    assert runtime_store.settings(local_app.server.config.app.state.store) == {
        "max_allocations": expected
    }
    assert not local_app.server.config.app.state.store.all("SELECT id FROM jobs")


def test_failed_capacity_save_before_close_can_be_explicitly_retried_after_reopen(page, local_app):
    held, limit = begin_capacity_save(page, local_app)
    reopen_ucloud(page)
    held[0].fulfill(status=503, json={"detail": "Synthetic earlier capacity save failed"})
    expect(limit).to_be_enabled()
    expect(limit).to_have_value("2")
    limit.fill("4")
    limit.press("Enter")
    page.wait_for_function("() => window.capacityWrites === 2")
    assert held[1].request.post_data_json == {"max_allocations": 4}
    commit(held[1])
    expect(page.locator("#runtime-capacity-status")).to_have_text(
        "Saved. Active allocations keep running."
    )
    expect(limit).to_be_enabled()
    expect(limit).to_have_value("4")
    assert runtime_store.settings(local_app.server.config.app.state.store) == {"max_allocations": 4}


def test_only_latest_reopened_capacity_view_reads_after_the_pending_save(page, local_app):
    held, limit = begin_capacity_save(page, local_app)
    reopen_ucloud(page)
    reopen_ucloud(page)
    assert page.evaluate("window.capacitySends.filter(method => method === 'GET').length") == 0
    commit(held[0])
    expect(limit).to_be_enabled()
    expect(limit).to_have_value("3")
    assert page.evaluate("window.capacitySends.filter(method => method === 'GET').length") == 1
    assert runtime_store.settings(local_app.server.config.app.state.store) == {"max_allocations": 3}
