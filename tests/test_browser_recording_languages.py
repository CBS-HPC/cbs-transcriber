"""Language metadata controls use the same revisioned save contract on both views."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber import enhancement, language_preferences
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def test_recording_inheritance_shows_and_tracks_project_language(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Inherited language", default_language="da")
    inherited = store.create_recording(project["id"], "Inherited.wav")
    explicit = store.create_recording(project["id"], "Explicit.wav")
    language_preferences.save(store, "recording", explicit["id"], "en", 0)
    page.add_init_script(
        """(() => {
          const original = window.fetch.bind(window);
          window.fetch = (url, options = {}) => {
            if (String(url) === '/api/projects' && window.holdProjectRead) {
              window.holdProjectRead = false;
              return original(url, options).then(async response => {
                const body = await response.text();
                return new Promise(resolve => {
                  window.releaseProjectRead = () => resolve(new Response(body,
                    {status: response.status, headers: {'Content-Type': 'application/json'}}));
                });
              });
            }
            return original(url, options);
          };
        })();"""
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card = page.locator(f'[data-recording-language="{inherited["id"]}"]')
    other = page.locator(f'[data-recording-language="{explicit["id"]}"]')
    expect(card.locator("option:checked")).to_have_text("Danish")
    expect(card.locator('option[value="da"]')).to_have_text("Danish (recording override)")
    expect(other.locator("option:checked")).to_have_text("English")

    page.evaluate(
        "() => { window.holdProjectRead = true; window.pendingProjectRead = refreshProjects(); }"
    )
    page.wait_for_function("() => typeof window.releaseProjectRead === 'function'")
    page.locator("#project-settings-open").click()
    page.locator("#project-edit-language").select_option("en")
    expect(page.locator("#project-edit-language")).to_have_value("en")
    expect(page.locator("#project-edit-language")).to_be_enabled()
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    expect(card.locator("option:checked")).to_have_text("English")
    expect(other.locator("option:checked")).to_have_text("English")
    expect(other.locator('option[value=""]')).to_have_text("Use project default (English)")
    page.evaluate("() => window.releaseProjectRead()")
    page.evaluate("() => window.pendingProjectRead")
    expect(card.locator("option:checked")).to_have_text("English")
    expect(other.locator("option:checked")).to_have_text("English")

    other.select_option("")
    expect(other.locator("option:checked")).to_have_text("English")
    expect(other.locator('option[value="en"]')).to_have_text("English (recording override)")
    expect(other).to_have_value("")
    page.get_by_role("button", name="Inherited", exact=True).click()
    editor = page.locator("#file-language")
    expect(editor.locator("option:checked")).to_have_text("English")
    language_preferences.save(store, "project", project["id"], None, 1)
    page.evaluate("() => refreshProjects()")
    expect(editor.locator("option:checked")).to_have_text(
        "Use model/processing settings (no project default)"
    )
    expect(editor).to_have_value("")


def test_saved_edit_reports_unavailable_automatic_word_timing(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Unsupported timing", default_language="fr")
    recording = store.create_recording(project["id"], "Interview.wav")
    rid = recording["id"]
    result = store.add_result(
        rid,
        normalize_segments([{"text": "Bonjour.", "start": 0, "end": 2}]),
        {},
        "Synthetic transcript",
        {},
    )

    def patch(route):
        response = route.fetch()
        body = response.json()
        body["realign_warning"] = "Automatic word timing does not support this language."
        body["snapshot_error"] = "Saved in the library database, but project outputs failed."
        route.fulfill(response=response, json=body)

    page.route(f"**/api/recordings/{rid}/segments/*/edit-v3", patch)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    page.locator("#passage-0 textarea").fill("Bonjour à tous.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Bonjour à tous.")
    expect(page.locator("#save-status")).to_contain_text("Automatic word timing does not support")
    expect(page.locator("#save-status")).to_contain_text("project outputs failed")
    expect(page.locator("#save-status")).to_be_visible()
    expect(page.locator("#toast")).to_contain_text("Passage saved.")
    assert store.result(rid, result["id"])["segments"][0]["text"] == "Bonjour à tous."


def test_saved_edit_displays_real_backend_language_warning(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Unsupported automatic timing", default_language="fr")
    recording = store.create_recording(project["id"], "Interview.wav")
    rid = recording["id"]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {
                    "text": "Bonjour.",
                    "start": 0,
                    "end": 2,
                    "words": [{"word": "Bonjour.", "start": 0, "end": 2}],
                }
            ]
        ),
        {},
        "Synthetic transcript",
        {"capability": "words"},
    )
    enhancement.save_realign(store, True)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Set edited version as Working"
    ).click()
    page.locator("#passage-0 textarea").fill("Bonjour à tous.")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("Bonjour à tous.")
    expect(page.locator("#save-status")).to_contain_text("Danish and English only")
    expect(page.locator("#save-status")).to_be_visible()
    assert store.result(rid, result["id"])["segments"][0]["text"] == "Bonjour à tous."
    assert not store.all("SELECT id FROM jobs WHERE recording_id=? AND kind='enhance'", (rid,))


def test_recording_language_saves_on_card_and_in_editor(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Languages")
    recording = store.create_recording(project["id"], "Interview.wav")
    saved = {"language": None, "revision": 0}
    requests = []

    def save(route):
        body = route.request.post_data_json
        requests.append(body)
        saved.update(language=body["language"], revision=body["revision"] + 1)
        route.fulfill(json=saved)

    def read_recording(route):
        response = route.fetch()
        body = response.json()
        body["language_override"] = saved["language"]
        body["language_revision"] = saved["revision"]
        route.fulfill(response=response, json=body)

    page.route(f"**/api/recordings/{recording['id']}/language", save)
    page.route(f"**/api/recordings/{recording['id']}", read_recording)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    card_language = page.locator(f'[data-recording-language="{recording["id"]}"]')
    expect(card_language).to_have_value("")
    card_language.select_option("da")
    expect(card_language).to_have_value("da")
    expect(card_language).to_be_enabled()
    assert requests == [{"language": "da", "revision": 0}]

    # The detail view may observe a newer language revision than the card snapshot.
    saved["revision"] = 3
    page.get_by_role("button", name="Interview", exact=True).click()
    language = page.locator("#file-language")
    expect(language).to_have_value("da")
    language.select_option("en")
    expect(language).to_have_value("en")
    expect(language).to_be_enabled()
    assert requests[-1] == {"language": "en", "revision": 3}


def test_search_other_language_for_new_project(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Existing")
    posted = []
    page.route(
        "**/api/languages",
        lambda route: route.fulfill(
            json={
                "languages": [
                    {"code": "da", "name": "Danish", "aliases": []},
                    {"code": "en", "name": "English", "aliases": []},
                    {"code": "yue", "name": "Yue Chinese", "aliases": ["Cantonese"]},
                ]
            }
        ),
    )

    def projects(route):
        if route.request.method == "POST":
            posted.append(route.request.post_data_json)
            route.fulfill(json={"id": project["id"]})
        else:
            route.continue_()

    page.route("**/api/projects", projects)
    page.goto(local_app.url)
    page.locator("#sidebar-create").click()
    page.locator("#name-input").fill("Language project")
    page.locator("#project-classification").select_option("standard")
    page.locator("#project-default-language").select_option("__choose__")
    expect(page.locator("#language-picker-dialog")).to_be_visible()
    page.locator("#language-picker-search").fill("Cantonese")
    expect(page.locator("#language-picker-list option")).to_have_count(1)
    page.locator("#language-picker-list").select_option("yue")
    page.locator("#language-picker-form button[type=submit]").click()
    expect(page.locator("#project-default-language")).to_have_value("yue")
    expect(page.locator("#project-default-language option:checked")).to_have_text("Yue Chinese")
    page.locator("#name-form button[type=submit]").click()
    expect(page.locator("#name-dialog")).to_be_hidden()
    assert posted[0]["default_language"] == "yue"


def test_existing_project_default_is_saved_independently(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Project language")
    sent = []

    def save(route):
        sent.append(route.request.post_data_json)
        route.fulfill(json={"language": "da", "revision": 1})

    page.route(f"**/api/projects/{project['id']}/language", save)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator("#project-settings-open").click()
    select = page.locator("#project-edit-language")
    expect(select).to_have_value("")
    select.select_option("da")
    expect(select).to_be_enabled()
    expect(select).to_have_value("da")
    assert sent == []
    page.locator("#project-settings-save").click()
    expect(page.locator("#project-settings-dialog")).not_to_be_visible()
    assert sent == [{"language": "da", "revision": 0}]


def test_stale_project_refresh_preserves_independent_language_and_notes(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Independent revisions")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator("#project-title")).to_have_text("Independent revisions")
    page.locator("#project-notes-panel summary").click()
    page.locator("#project-notes").fill("Unsent draft")
    page.evaluate(
        """async () => {
          const newerNotes = {...state.project, notes: 'Saved note', notes_revision: 10,
                              default_language: 'da', language_revision: 2};
          const newerLanguage = {...state.project, notes: 'Old note', notes_revision: 1,
                                 default_language: 'en', language_revision: 9};
          state.project = newerNotes;
          state.projects = state.projects.map(p => p.id === newerLanguage.id ? newerLanguage : p);
          await refreshProjects();
        }"""
    )
    assert page.evaluate("state.project.notes") == "Saved note"
    assert page.evaluate("state.project.notes_revision") == 10
    assert page.evaluate("state.project.default_language") == "en"
    assert page.evaluate("state.project.language_revision") == 9
    page.locator("#project-settings-open").click()
    expect(page.locator("#project-edit-language")).to_have_value("en")
    expect(page.locator("#project-notes")).to_have_value("Unsent draft")


def test_delayed_recording_read_keeps_completed_card_language_save(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Delayed detail")
    recording = store.create_recording(project["id"], "Interview.wav")
    page.add_init_script(
        """(() => {
          const original = window.fetch.bind(window);
          window.fetch = (url, options = {}) => {
            if (String(url).endsWith('/language') && options.method === 'PUT') {
              const body = JSON.parse(options.body);
              window.lastLanguageBody = body;
              return new Promise(resolve => {
                window.finishLanguagePut = () => resolve(new Response(
                  JSON.stringify({language: body.language, revision: body.revision + 1}),
                  {status: 200, headers: {'Content-Type': 'application/json'}}));
              });
            }
            if (String(url).endsWith('/recordings/RECORDING_ID') && !options.method) {
              return original(url, options).then(async response => {
                const body = await response.text();
                return new Promise(resolve => {
                  window.finishRecordingGet = () => resolve(new Response(body,
                    {status: response.status, headers: {'Content-Type': 'application/json'}}));
                });
              });
            }
            return original(url, options);
          };
        })();""".replace("RECORDING_ID", recording["id"])
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    language = page.locator(f'[data-recording-language="{recording["id"]}"]')
    language.select_option("da")
    page.wait_for_function("() => typeof window.finishLanguagePut === 'function'")
    page.get_by_role("button", name="Interview", exact=True).click()
    page.wait_for_function("() => typeof window.finishRecordingGet === 'function'")
    page.evaluate("() => window.finishLanguagePut()")
    page.wait_for_function("() => !document.querySelector('[data-recording-language]').disabled")
    page.evaluate("() => window.finishRecordingGet()")
    editor_language = page.locator("#file-language")
    expect(editor_language).to_have_value("da")
    editor_language.select_option("en")
    page.wait_for_function("() => window.lastLanguageBody.language === 'en'")
    assert page.evaluate("() => window.lastLanguageBody.revision") == 1
    page.evaluate("() => window.finishLanguagePut()")
    expect(editor_language).to_have_value("en")


def test_transcription_waits_for_recording_language_save(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Language saves")
    recording = store.create_recording(project["id"], "Interview.wav")
    page.add_init_script(
        """(() => {
          const original = window.fetch.bind(window);
          window.fetch = (url, options) => {
            if (String(url).endsWith('/language') && options?.method === 'PUT') {
              const body = JSON.parse(options.body);
              return new Promise(resolve => {
                window.completeLanguageSave = () => resolve(new Response(
                  JSON.stringify({language: body.language, revision: body.revision + 1}),
                  {status: 200, headers: {'Content-Type': 'application/json'}}));
              });
            }
            return original(url, options);
          };
        })();"""
    )
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    language = page.locator("#file-language")
    expect(language).to_be_enabled()
    language.select_option("da")
    expect(language).to_be_disabled()
    expect(page.locator("#transcribe-one")).to_be_disabled()
    page.wait_for_function("typeof window.completeLanguageSave === 'function'")
    page.evaluate("window.completeLanguageSave()")
    expect(language).to_be_enabled()
    expect(language).to_have_value("da")


@pytest.mark.parametrize("scope", ["recording", "project"])
def test_processing_and_edits_wait_for_pending_language_save(page, local_app, scope):
    store = local_app.server.config.app.state.store
    project = store.create_project("Pending language", default_language="da")
    recording = store.create_recording(project["id"], "Interview.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=30 WHERE id=?", (rid,))
    values = [-0.1, -0.12, -0.9, -0.11, -0.13, -0.1, -0.14, -0.12, -0.1, -0.11]
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": f"Passage {i}.", "start": i * 2, "end": i * 2 + 1.5, "avg_logprob": value}
                for i, value in enumerate(values)
            ]
        ),
        {},
        "Synthetic transcript",
        {"capability": "words"},
        auto_first=True,
    )
    enhancement.save_realign(store, True)
    pending, processing = [], []
    target = project["id"] if scope == "project" else rid

    def hold(route):
        if route.request.method == "PUT":
            pending.append(route)
            page.evaluate("() => { window.languageWriteHeld = true; }")
        else:
            route.continue_()

    page.route(f"**/api/{scope}s/{target}/language", hold)
    page.on(
        "request",
        lambda request: (
            processing.append((request.method, request.url))
            if request.method in {"POST", "PATCH"}
            and any(
                part in request.url
                for part in ("/passage-timing", "/enhancements", "/retranscriptions", "/segments/")
            )
            else None
        ),
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    if scope == "project":
        page.locator("#project-settings-open").click()
        page.locator("#project-edit-language").select_option("en")
        page.locator("#project-settings-save").click()
        page.wait_for_function("() => window.languageWriteHeld === true")
        expect(page.locator("#project-settings-cancel")).to_be_disabled()
        message = page.evaluate(
            """async rid => {
              try { await api('/transcriptions', {method: 'POST', body: {recording_ids: [rid]}}); }
              catch (error) { return error.message; }
              return '';
            }""",
            rid,
        )
        assert "Wait for language settings" in message
        assert not processing
        [write] = pending
        write.fulfill(response=write.fetch())
        expect(page.locator("#project-settings-dialog")).not_to_be_visible()
        return
    page.get_by_role("button", name="Interview", exact=True).click()
    if scope == "recording":
        page.locator("#file-language").select_option("en")
        page.wait_for_function("() => window.languageWriteHeld === true")

    actions = [
        ("#passage-0 [data-run-repair]", False),
        ("#timing-warning [data-run-repair]", False),
        ("#speaker-tag", True),
        ("#passage-4 [data-action='retranscribe']", False),
        ("#retranscribe-flagged", True),
    ]
    for selector, confirm_action in actions:
        page.evaluate("() => { document.querySelector('#toast').textContent = ''; }")
        if confirm_action:
            page.once("dialog", lambda dialog: dialog.accept())
        page.locator(selector).click()
        expect(page.locator("#toast")).to_contain_text("Wait for language settings")
        assert not processing

    page.locator("#passage-0 [data-action='edit']").click()
    draft = page.locator("#passage-0 textarea")
    draft.fill("An unsaved correction.")
    page.evaluate("() => { document.querySelector('#toast').textContent = ''; }")
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#toast")).to_contain_text("Wait for language settings")
    expect(draft).to_have_value("An unsaved correction.")
    expect(draft).to_be_enabled()
    assert not processing
    assert store.result(rid, result["id"])["segments"][0]["text"] == "Passage 0."

    [write] = pending
    write.fulfill(response=write.fetch())
    page.wait_for_function(
        "() => pendingRecordingLanguages.size === 0 && pendingProjectLanguages.size === 0"
    )
    # The guard was exercised with realignment enabled; avoid launching a worker in this UI test.
    enhancement.save_realign(store, False)
    page.locator("#passage-0 [data-action='save-edit']").click()
    expect(page.locator("#passage-0 .passage-text")).to_have_text("An unsaved correction.")
    assert store.result(rid, result["id"])["segments"][0]["text"] == "An unsaved correction."


def test_language_save_started_during_processing_preflight_blocks_enqueue(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Language during preflight")
    recording = store.create_recording(project["id"], "Interview.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    store.add_result(
        rid,
        normalize_segments([{"text": "Hello.", "start": 0, "end": 4}]),
        {},
        "Synthetic transcript",
        {},
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    expect(page.locator("#file-language")).to_be_enabled()
    held_settings, held_language, processing = [], [], []

    def settings(route):
        if route.request.method == "GET" and not held_settings:
            held_settings.append(route)
            page.evaluate("() => { window.settingsReadHeld = true; }")
        else:
            route.continue_()

    def language(route):
        held_language.append(route)
        page.evaluate("() => { window.languageWriteHeld = true; }")

    page.route("**/api/enhancement-settings", settings)
    page.route(f"**/api/recordings/{rid}/language", language)
    page.on(
        "request",
        lambda request: (
            processing.append(request.url)
            if request.method == "POST" and request.url.endswith("/retranscriptions")
            else None
        ),
    )
    page.locator("#passage-0 [data-action='retranscribe']").click()
    page.wait_for_function("() => window.settingsReadHeld === true")
    page.locator("#file-language").select_option("en")
    page.wait_for_function("() => window.languageWriteHeld === true")
    held_settings[0].fulfill(response=held_settings[0].fetch())
    expect(page.locator("#toast")).to_contain_text("Wait for language settings")
    assert not processing
    held_language[0].fulfill(response=held_language[0].fetch())
    expect(page.locator("#file-language")).to_have_value("en")
    expect(page.locator("#file-language")).to_be_enabled()


def test_batch_cannot_start_while_selected_recording_language_is_saving(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Batch languages")
    recording = store.create_recording(project["id"], "Interview.wav")
    page.add_init_script(
        """(() => {
          const original = window.fetch.bind(window);
          window.fetch = (url, options) => {
            if (String(url).endsWith('/language') && options?.method === 'PUT') {
              const body = JSON.parse(options.body);
              return new Promise(resolve => {
                window.completeLanguageSave = () => resolve(new Response(
                  JSON.stringify({language: body.language, revision: body.revision + 1}),
                  {status: 200, headers: {'Content-Type': 'application/json'}}));
              });
            }
            return original(url, options);
          };
        })();"""
    )
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f'[data-select="{recording["id"]}"]').check()
    language = page.locator(f'[data-recording-language="{recording["id"]}"]')
    language.select_option("da")
    expect(language).to_be_disabled()
    expect(page.locator("#transcribe-selected")).to_be_disabled()
    page.locator("#transcribe-selected").evaluate(
        "e => e.dispatchEvent(new MouseEvent('click', {bubbles: true}))"
    )
    expect(page.locator("#toast")).to_contain_text("Wait for language settings")
    page.wait_for_function("typeof window.completeLanguageSave === 'function'")
    page.evaluate("window.completeLanguageSave()")
    expect(language).to_be_enabled()
    expect(language).to_have_value("da")


def test_language_and_transcribe_controls_fit_wide_view(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Layout")
    recording = store.create_recording(project["id"], "Interview.wav")
    page.set_viewport_size({"width": 1920, "height": 1000})
    page.goto(local_app.url + f"/#/recordings/{recording['id']}")
    expect(page.locator("#file-language")).to_be_visible()
    expect(page.get_by_role("combobox", name="Language for this recording")).to_be_visible()
    assert (
        page.locator(".recording-language-choice").evaluate(
            "element => [...element.childNodes].filter(node => node.nodeType === Node.TEXT_NODE)"
            ".map(node => node.textContent).join('').trim()"
        )
        == ""
    )
    sizes = page.evaluate(
        """() => Object.fromEntries(
          ['.editor-actions', '#file-provider-kind', '#file-provider', '#file-features',
           '#file-language', '.editor-action-buttons', '.transcript-search'].map(s => {
            const r = document.querySelector(s).getBoundingClientRect();
            return [s, {x:r.x, y:r.y, width:r.width, height:r.height}];
          }))"""
    )
    assert sizes["#file-language"]["x"] < sizes[".editor-action-buttons"]["x"], sizes
    assert abs(sizes[".transcript-search"]["y"] - sizes[".editor-action-buttons"]["y"]) < 5, sizes
    for selector in ("#file-provider-kind", "#file-provider", "#file-features", "#file-language"):
        item = sizes[selector]
        search = sizes[".transcript-search"]
        assert abs(item["y"] + item["height"] / 2 - search["y"] - search["height"] / 2) < 5, sizes
    # Native UCloud models show both included feature descriptions.
    page.locator("#file-features").evaluate(
        """e => e.innerHTML = `<div class="transcription-features">${
          ['Word timing', 'Speaker labels'].map(label => `<label>
            <input type="checkbox" disabled checked>${label}
            <span class="feature-included">· included</span></label>`).join('')
        }</div>`"""
    )
    page.locator("#file-provider-kind").evaluate("e => e.selectedOptions[0].textContent = 'UCloud'")
    page.locator("#file-provider").evaluate(
        "e => e.selectedOptions[0].textContent = 'whisper/large-v3-turbo (Transcriber)'"
    )
    page.set_viewport_size({"width": 1476, "height": 900})
    sizes = page.evaluate(
        """() => Object.fromEntries(
          ['.editor-actions', '#file-provider-kind', '#file-provider', '#file-features',
           '#file-language', '.editor-action-buttons', '.transcript-search'].map(s => {
            const r = document.querySelector(s).getBoundingClientRect();
            return [s, {x:r.x, y:r.y, width:r.width, height:r.height}];
          }))"""
    )
    assert abs(sizes[".transcript-search"]["y"] - sizes[".editor-action-buttons"]["y"]) < 5, sizes
    for selector in ("#file-provider-kind", "#file-provider", "#file-features", "#file-language"):
        item = sizes[selector]
        search = sizes[".transcript-search"]
        assert abs(item["y"] + item["height"] / 2 - search["y"] - search["height"] / 2) < 5, sizes
    for width in (1280, 1300, 1450, 1460, 1470, 1476, 1480, 1600):
        page.set_viewport_size({"width": width, "height": 900})
        row = page.evaluate(
            """() => Object.fromEntries(
              ['#file-provider-kind', '#file-provider', '#file-features', '#file-language',
               '#transcribe-one', '#working-mark-current', '.transcript-search'].map(selector => {
                const rect = document.querySelector(selector).getBoundingClientRect();
                return [selector, {x: rect.x, y: rect.y, width: rect.width, height: rect.height}];
              }))"""
        )
        search = row[".transcript-search"]
        for selector, rect in row.items():
            assert abs(rect["y"] + rect["height"] / 2 - search["y"] - search["height"] / 2) < 5, (
                width,
                selector,
                row,
            )
        features, language = row["#file-features"], row["#file-language"]
        assert 0 <= language["x"] - features["x"] - features["width"] <= 10, (width, row)
        transcribe = row["#transcribe-one"]
        gap = transcribe["x"] - language["x"] - language["width"]
        assert 5 <= gap <= 12, (width, row)
    # Test the available editor width too: the sidebar, display scaling and a
    # side panel can make it much narrower than the browser viewport.
    page.set_viewport_size({"width": 1600, "height": 900})
    editor = page.locator("#editor-view")
    editor.evaluate("e => e.style.width = '1100px'")
    row = page.evaluate(
        """() => Object.fromEntries(
          ['.editor-actions', '#file-provider-kind', '#file-provider', '#file-features',
           '#file-language', '#transcribe-one', '#working-mark-current',
           '.transcript-search'].map(selector => {
            const rect = document.querySelector(selector).getBoundingClientRect();
            return [selector, {left: rect.left, right: rect.right,
                               center: rect.top + rect.height / 2}];
          }))"""
    )
    toolbar = row.pop(".editor-actions")
    for selector, rect in row.items():
        assert abs(rect["center"] - row[".transcript-search"]["center"]) < 5, (selector, row)
        assert toolbar["left"] <= rect["left"] and rect["right"] <= toolbar["right"] + 1, row
    editor.evaluate("e => e.style.width = ''")
    page.set_viewport_size({"width": 390, "height": 900})
    provider = page.locator("#file-provider-kind").bounding_box()
    search = page.locator(".transcript-search").bounding_box()
    assert search["y"] > provider["y"] + provider["height"]
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    # The compact layout must not wrap at either side of its viewport breakpoints.
    for width in (
        390,
        420,
        500,
        580,
        581,
        600,
        760,
        990,
        1050,
        1100,
        1200,
        1250,
        1299,
        1300,
        1349,
        1350,
        1400,
        1450,
        1451,
        1476,
        1700,
        1701,
        1711,
        1850,
    ):
        page.set_viewport_size({"width": width, "height": 900})
        buttons = page.locator(".editor-action-buttons").bounding_box()
        search = page.locator(".transcript-search").bounding_box()
        assert abs(buttons["y"] + buttons["height"] / 2 - search["y"] - search["height"] / 2) < 5, (
            width,
            buttons,
            search,
        )
        assert search["x"] >= buttons["x"] + buttons["width"] - 1, (width, buttons, search)
    for width in (1920, 1850, 1711, 1701, 1700, 1476, 1350, 1349, 1100, 760, 390):
        page.set_viewport_size({"width": width, "height": 900})
        expect(page.locator("#file-language")).to_be_visible()
        expect(page.locator("#transcribe-one")).to_be_visible()
        expect(page.locator(".transcript-search")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        assert page.evaluate(
            """() => {
              const elements = [...document.querySelector('.editor-actions').children];
              const rects = elements.map(e => e.getBoundingClientRect());
              return rects.every((a, i) => rects.slice(i + 1).every(b =>
                a.right <= b.left + 1 || b.right <= a.left + 1 ||
                a.bottom <= b.top + 1 || b.bottom <= a.top + 1));
            }"""
        ), width
