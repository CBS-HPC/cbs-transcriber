"""Bulk selection and language controls use exact, durable recording revisions."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def test_select_all_waits_until_project_recordings_load(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delayed recordings")
    store.create_recording(project["id"], "one.wav")
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options = {}) => {
        if (String(url).includes('/projects/') && String(url).endsWith('/recordings'))
          return new Promise((resolve, reject) => {
            window.releaseRecordingList = () => original(url, options).then(resolve, reject);
          });
        return original(url, options);
      };
    })()""")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator("#select-all")).to_be_disabled()
    page.wait_for_function("() => typeof window.releaseRecordingList === 'function'")
    page.evaluate("window.releaseRecordingList()")
    expect(page.locator("#select-all")).to_be_enabled()
    expect(page.get_by_role("button", name="Select all", exact=True)).to_be_visible()
    expect(page.locator("#selection-count")).to_have_count(0)
    assert page.locator("#add-files").evaluate(
        "element => element.parentElement === "
        "document.querySelector('#transcribe-selected').parentElement"
    )
    page.locator("#select-all").click()
    expect(page.locator("[data-select]:checked")).to_have_count(1)
    expect(page.locator("#select-all")).to_be_disabled()
    page.locator("[data-select]").uncheck()
    expect(page.locator("#select-all")).to_be_enabled()
    page.locator("[data-select]").check()
    expect(page.locator("#select-all")).to_be_disabled()


def test_project_switch_does_not_select_previous_project_cards(page, local_app):
    store = local_app.server.config.app.state.store
    first, second = store.create_project("First"), store.create_project("Second")
    old = store.create_recording(first["id"], "old.wav")
    new = store.create_recording(second["id"], "new.wav")
    page.add_init_script(
        """(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options = {}) => {
        if (String(url).endsWith('/projects/PROJECT_ID/recordings'))
          return new Promise((resolve, reject) => {
            window.releaseRecordingList = () => original(url, options).then(resolve, reject);
          });
        return original(url, options);
      };
    })()""".replace("PROJECT_ID", second["id"])
    )
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    expect(page.locator(f'[data-select="{old["id"]}"]')).to_be_enabled()
    page.evaluate("id => { location.hash = '#/projects/' + id; }", second["id"])
    expect(page.locator("#project-title")).to_have_text("Second")
    expect(page.locator("#select-all")).to_be_disabled()
    expect(page.locator(f'[data-select="{old["id"]}"]')).to_be_disabled()
    expect(page.locator(f'[data-select="{old["id"]}"]')).not_to_be_visible()
    page.wait_for_function("() => typeof window.releaseRecordingList === 'function'")
    page.evaluate("window.releaseRecordingList()")
    expect(page.locator(f'[data-select="{new["id"]}"]')).to_be_enabled()
    page.locator("#select-all").click()
    expect(page.locator("[data-select]:checked")).to_have_count(1)


def test_filtered_selection_bulk_language_and_deselect_all(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Bulk languages")
    rows = [
        store.create_recording(project["id"], name) for name in ("one.wav", "two.wav", "three.wav")
    ]
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-select="{rows[2]["id"]}"]').check()
    page.locator("#recording-search").fill("one")
    page.locator("#select-all").click()
    expect(page.locator("[data-select]:checked")).to_have_count(1)
    expect(page.locator("#transcribe-selected")).to_have_attribute(
        "title", "2 selected; 1 hidden by the current filter"
    )
    page.locator("#bulk-language").select_option("en")
    expect(page.locator("#bulk-language-status")).to_contain_text("saved for 2")
    assert [store.recording(row["id"])["language_override"] for row in rows] == ["en", None, "en"]
    page.locator("#recording-search").fill("")
    expect(page.locator("[data-select]:checked")).to_have_count(2)
    page.locator("#deselect-all").click()
    expect(page.locator("[data-select]:checked")).to_have_count(0)
    expect(page.locator("#bulk-language")).to_be_disabled()


def test_bulk_language_picker_and_revision_conflict_leave_selection_intact(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Bulk picker")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-language").select_option("__choose__")
    page.locator("#language-picker-search").fill("French")
    page.locator("#language-picker-list").select_option("fr")
    page.locator("#language-picker-form button[type=submit]").click()
    expect(page.locator("#bulk-language-status")).to_contain_text("saved for 2")
    assert all(store.recording(row["id"])["language_override"] == "fr" for row in rows)
    page.locator("#bulk-language").select_option("__inherit__")
    expect(page.locator("#bulk-language-status")).to_contain_text("saved for 2")
    expect(page.locator(f'[data-recording-language="{rows[0]["id"]}"]')).to_have_value("")
    store.execute(
        "UPDATE recordings SET language_override='en',"
        "language_revision=language_revision+1 WHERE id=?",
        (rows[1]["id"],),
    )
    page.locator("#bulk-language").select_option("da")
    expect(page.locator("#bulk-language-status")).to_contain_text("changed")
    expect(page.locator("[data-select]:checked")).to_have_count(2)
    assert store.recording(rows[0]["id"])["language_override"] is None
    assert store.recording(rows[1]["id"])["language_override"] == "en"


def test_bulk_save_blocks_selection_transcription_and_navigation_until_settled(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Pending bulk")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options = {}) => {
        if (String(url).endsWith('/recording-languages') && options.method === 'PUT')
          return new Promise((resolve, reject) => {
            window.releaseBulkLanguage = () => original(url, options).then(resolve, reject);
          });
        return original(url, options);
      };
    })()""")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-language").select_option("da")
    expect(page.locator("#bulk-language-status")).to_contain_text("Saving")
    expect(page.locator("#select-all")).to_be_disabled()
    expect(page.locator("#deselect-all")).to_be_disabled()
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    blocked = page.evaluate(
        "id => api('/recordings/' + id + '/enhancements', "
        "{method: 'POST', body: {}}).then(() => '', e => e.message)",
        rows[0]["id"],
    )
    assert "language settings" in blocked
    page.evaluate("id => { location.hash = '#/recordings/' + id; }", rows[0]["id"])
    expect(page).to_have_url(local_app.url + f"/#/projects/{project['id']}")
    page.evaluate("window.releaseBulkLanguage()")
    expect(page.locator("#bulk-language-status")).to_contain_text("saved for 2")
    assert all(store.recording(row["id"])["language_override"] == "da" for row in rows)


def test_lost_save_reply_requires_reload_before_retry(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Lost reply")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]

    def lose_reply(route):
        response = route.fetch()
        assert response.ok
        route.abort("connectionreset")

    page.route(f"**/api/projects/{project['id']}/recording-languages", lose_reply)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-language").select_option("da")
    expect(page.locator("#bulk-language-status")).to_contain_text("Could not confirm")
    expect(page.locator("#bulk-language")).to_be_disabled()
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    page.reload()
    expect(page.locator(f'[data-recording-language="{rows[0]["id"]}"]')).to_have_value("da")
    assert all(store.recording(row["id"])["language_override"] == "da" for row in rows)


def test_earlier_collection_move_does_not_clear_pending_bulk_selection(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Overlapping changes")
    rows = [store.create_recording(project["id"], name) for name in ("one.wav", "two.wav")]
    collection = store.create_collection(project["id"], "Destination")
    page.add_init_script("""(() => {
      const original = window.fetch.bind(window);
      window.fetch = (url, options = {}) => {
        if (String(url).endsWith('/collections/move') && options.method === 'POST')
          return new Promise((resolve, reject) => {
            window.releaseCollectionMove = () => original(url, options).then(resolve, reject);
          });
        return original(url, options);
      };
    })()""")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#select-all").click()
    page.locator("#bulk-collection").select_option(collection["id"])
    page.locator("#bulk-language").select_option("da")
    expect(page.locator("#bulk-language-status")).to_contain_text("saved for 2")
    page.evaluate("window.releaseCollectionMove()")
    expect(page.locator("#toast")).to_contain_text("Moved 2 recording")
    expect(page.locator("[data-select]:checked")).to_have_count(2)
    assert all(store.recording(row["id"])["language_override"] == "da" for row in rows)
