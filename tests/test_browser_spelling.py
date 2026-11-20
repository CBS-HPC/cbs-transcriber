"""Real spelling UI against tiny pinned dictionaries; no public downloads or private library.

ADR 0185 with the 20 November redesign (ADR 0186/0187): coloured words behind the textarea,
Ctrl+click or Alt+Enter for a suggestion popover, a remembered toolbar toggle, Activity
dictionary downloads and recording vocabulary.
"""

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import gettempdir
from threading import Event
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import test_browser
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from cbs_transcriber import language_preferences, spelling
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser
page = test_browser.page
CTRL = ["Control"]


@pytest.fixture(autouse=True)
def local_browser_only(page):
    refused = []

    def guard(route):
        if urlsplit(route.request.url).hostname == "127.0.0.1":
            route.continue_()
        else:
            refused.append(True)
            route.abort()

    page.route("http*://**/*", guard)
    yield
    assert not refused, "The spelling UI attempted an external browser request."


@pytest.fixture
def spelled(tmp_path, monkeypatch):
    original_catalog = spelling._catalog
    catalog = json.loads(
        (Path(spelling.__file__).parent / "catalogs/spelling_dictionaries.json").read_bytes()
    )
    bodies = {}
    for dictionary in catalog["dictionaries"]:
        words = ["hello", "world", "correct", "word", "new", "draft", "hej", "verden"]
        data = {
            "aff": b"SET UTF-8\nTRY esiarntolcdugmphbyfvkwzxjq\n",
            "dic": (str(len(words)) + "\n" + "\n".join(words) + "\n").encode(),
            "license": b"Synthetic dictionary fixture licence. No publisher dictionary bytes.\n",
        }
        for kind, content in data.items():
            entry = dictionary["files"][kind]
            entry.update(bytes=len(content), sha256=sha256(content).hexdigest())
            bodies[entry["url"]] = content
    monkeypatch.setattr(
        spelling,
        "_catalog",
        lambda value=None: original_catalog(catalog if value is None else value),
    )
    downloads, gate, control = [], Event(), SimpleNamespace(fail=False)
    gate.set()

    def download(entry, target, *, guard=None):
        assert entry["url"] in bodies, "A test tried to download a public dictionary."
        # A cleared gate holds the synthetic download; shutdown still stops it.
        while not gate.wait(0.02):
            if guard:
                guard()
        if guard:
            guard()
        if control.fail:
            raise OSError("Synthetic offline dictionary host")
        downloads.append(deepcopy(entry))
        target.write_bytes(bodies[entry["url"]])

    monkeypatch.setattr(spelling, "_download_file", download)
    create_app = test_browser.create_app
    monkeypatch.setattr(
        test_browser,
        "create_app",
        lambda root, **kwargs: create_app(root, spelling_cache_dir=tmp_path / "cache", **kwargs),
    )
    app = test_browser.LocalApp(tmp_path / "library")
    app.start()
    store = app.server.config.app.state.store
    project = store.create_project("Private synthetic vocabulary", default_language="en")
    rid = store.create_recording(project["id"], "first.wav")["id"]
    segments = normalize_segments(
        [
            {
                "text": "😀 hello helllo world",
                "start": 0,
                "end": 3,
                "words": [
                    {"word": "😀", "start": 0, "end": 0.3},
                    {"word": "hello", "start": 0.3, "end": 1},
                    {"word": "helllo", "start": 1, "end": 2},
                    {"word": "world", "start": 2, "end": 3},
                ],
            },
            {"text": "hello world", "start": 3, "end": 4},
        ]
    )
    result = store.add_result(rid, segments, {}, "Synthetic", {}, auto_first=True)
    other = store.create_recording(project["id"], "second.wav")["id"]
    store.add_result(
        other, normalize_segments([{"text": "hello world"}]), {}, "Other", {}, auto_first=True
    )
    fixture = SimpleNamespace(
        app=app,
        store=store,
        service=app.server.config.app.state.spelling,
        project=project["id"],
        rid=rid,
        result=result["id"],
        other=other,
        downloads=downloads,
        gate=gate,
        control=control,
        cache=tmp_path / "cache",
    )
    yield fixture
    gate.set()
    app.stop()


def request(fixture, method, path, body=None):
    response = httpx.request(
        method,
        fixture.app.url + "/api" + path,
        json=body,
        headers={
            "X-CBS-Request": "1",
            "X-CBS-Instance": fixture.app.server.config.app.state.browser_instance,
            "Origin": fixture.app.url,
        },
    )
    assert response.is_success, response.text
    return response.json()


def quietly(fixture, *, enabled=True, auto_download=True, install=()):
    """Save preferences and install dictionaries without the HTTP automatic trigger."""
    current = fixture.service.settings()["revision"]
    fixture.service.save_settings(current, enabled, auto_download)
    for language in install:
        fixture.service.install_language(language, consent=True)


def editor(page, fixture, rid=None, passage=0):
    page.goto(fixture.app.url + f"/#/recordings/{rid or fixture.rid}")
    page.locator(f'#passage-{passage} [data-action="edit"]').click()
    expect(page.locator("[data-spelling-panel]")).to_be_visible()
    return page.locator(f'#passage-{passage} [data-editor-pane="text"] textarea')


def marks(page):
    return page.locator(".passage-spelling-overlay .spelling-flag")


def status(page):
    return page.locator("[data-spelling-status]")


def ready(page, *words):
    expect(marks(page)).to_have_text(list(words or ["helllo"]), timeout=15000)


def popover(page):
    return page.locator("[data-spelling-popover]")


def ctrl_click(page, index=0):
    """Ctrl+click the middle of a coloured word, through the textarea above it."""
    mark = marks(page).nth(index).bounding_box()
    draft = page.locator('[data-editor-pane="text"]:not([hidden]) textarea')
    box = draft.bounding_box()
    draft.click(
        position={
            "x": mark["x"] + mark["width"] / 2 - box["x"],
            "y": mark["y"] + mark["height"] / 2 - box["y"],
        },
        modifiers=CTRL,
    )
    expect(popover(page)).to_be_visible()


def settings(page):
    page.locator("#settings-open").click()
    page.locator("#tab-spelling").click()
    expect(page.locator("#spelling-settings-save")).to_be_enabled()


def close_settings(page):
    page.locator('#settings-dialog [aria-label="Close settings"]').click()
    expect(page.locator("#settings-dialog")).to_be_hidden()


def wait_held(page, held, count=1):
    deadline = monotonic() + 8
    while len(held) < count and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert len(held) >= count, "The synthetic request was never intercepted."


def release(held):
    route, response = held.pop(0)
    try:
        route.fulfill(response=response)
    except PlaywrightError as error:
        # Text changes intentionally abort in-flight check requests.
        assert "already handled" in str(error) or "Target" in str(error)


def activity_card(page):
    return page.locator("[data-spelling-task]")


def test_first_use_downloads_in_activity_and_suggestion_is_undoable_input(page, spelled):
    checks, dictionary_requests = [], []
    page.on(
        "request",
        lambda req: (
            checks.append(req.post_data_json)
            if req.url.endswith("/spelling/check")
            else dictionary_requests.append(req.post_data_json)
            if req.url.endswith("/spelling/dictionary-tasks") and req.method == "POST"
            else None
        ),
    )
    before = spelled.store.recording(spelled.rid)
    versions = len(spelled.store.result_versions(spelled.rid, spelled.result))
    spelled.gate.clear()
    draft = editor(page, spelled)
    enable = page.locator("[data-spelling-enable]")
    expect(enable).to_be_enabled()
    expect(enable).not_to_be_checked()
    # No fixed issue box: only the toolbar switch and a screen-reader status.
    expect(page.locator("[data-spelling-issues], .spelling-issues")).to_have_count(0)
    assert request(spelled, "GET", "/spelling/settings")["auto_download"] is True
    assert not checks and not dictionary_requests and not spelled.downloads
    assert draft.evaluate("node => node.spellcheck") is False
    enable.check()
    # Enabling queues the selected language's dictionary in Activity; the editor waits.
    expect(status(page)).to_contain_text("Follow it in Activity", timeout=8000)
    expect(status(page)).to_contain_text("no transcript text is sent")
    assert all(
        set(body) == {"settings_revision", "consent", "recording_id"}
        for body in (dictionary_requests)
    )
    assert all(body["consent"] is False for body in dictionary_requests)
    page.locator("#activity-open").click()
    card = activity_card(page)
    expect(card).to_contain_text("English (United Kingdom, -ise) spelling dictionary")
    expect(card).to_contain_text("No transcript text or vocabulary is sent")
    expect(card.locator(".badge")).to_have_text("running")
    assert not spelled.downloads
    spelled.gate.set()
    expect(card.locator(".badge")).to_have_text("ready", timeout=10000)
    expect(card.locator('[data-spelling-task-action="dismiss"]')).to_be_enabled()
    page.locator("#activity-close").click()
    ready(page)
    assert len(spelled.downloads) == 3
    expect(status(page)).to_contain_text("1 possible spelling issue coloured")
    expect(page.locator(".passage-spelling-toggle")).to_have_attribute(
        "title", status(page).text_content()
    )
    ctrl_click(page)
    expect(popover(page)).to_have_attribute("role", "dialog")
    expect(popover(page).locator(".spelling-popover-word")).to_have_text("helllo")
    suggestion = popover(page).locator("[data-spelling-suggestion]", has_text="hello").first
    expect(suggestion).to_be_visible(timeout=10000)
    draft.evaluate(
        "node => { window.spellingInputEvents = 0; "
        "node.addEventListener('input', () => window.spellingInputEvents++); }"
    )
    suggestion.click()
    expect(popover(page)).to_have_count(0)
    expect(draft).to_have_value("😀 hello hello world")
    assert draft.evaluate("node => node.selectionStart === 14 && node.selectionEnd === 14")
    assert page.evaluate("() => window.spellingInputEvents") == 1
    assert spelled.store.recording(spelled.rid) == before
    assert len(spelled.store.result_versions(spelled.rid, spelled.result)) == versions
    # The replacement is ordinary input: the browser's own Undo restores the word.
    page.keyboard.press("Control+z")
    expect(draft).to_have_value("😀 hello helllo world")
    ready(page)
    count = len(checks)
    draft.fill("hello neww")
    page.wait_for_timeout(120)
    draft.fill("hello draftt")
    page.wait_for_timeout(200)
    assert len(checks) == count
    ready(page, "draftt")
    assert checks[-1]["text"] == "hello draftt"
    assert all(set(body) == {"text", "request_id"} for body in checks)
    page.locator("[data-editor-mode]").select_option("timing")
    expect(page.locator("[data-spelling-panel]")).to_have_count(0)
    expect(page.locator(".passage-spelling-overlay")).to_have_count(0)
    count = len(checks)
    page.wait_for_timeout(600)
    assert len(checks) == count


def test_keyboard_route_focuses_popover_and_escape_returns_to_the_caret(page, spelled):
    quietly(spelled, install=["en"])
    draft = editor(page, spelled)
    ready(page)
    # The caret inside the coloured word (UTF-16 offset 11 lies within "helllo").
    draft.evaluate("node => { node.focus(); node.setSelectionRange(11, 11); }")
    page.keyboard.press("Alt+Enter")
    expect(popover(page)).to_be_visible()
    suggestion = popover(page).locator("[data-spelling-suggestion]").first
    expect(suggestion).to_be_focused(timeout=10000)
    page.keyboard.press("Escape")
    expect(popover(page)).to_have_count(0)
    expect(draft).to_be_focused()
    assert draft.evaluate("node => node.selectionStart === 11 && node.selectionEnd === 11")
    expect(draft).to_have_value("😀 hello helllo world")
    expect(page.locator("#passage-0 .passage-editor")).to_be_visible()
    # Ignore once is reachable from the keyboard as well.
    page.keyboard.press("Alt+Enter")
    expect(suggestion).to_be_focused(timeout=10000)
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    expect(popover(page).locator('[data-spelling-action="ignore"]')).to_be_focused()
    page.keyboard.press("Enter")
    expect(marks(page)).to_have_count(0)
    expect(status(page)).to_have_text("No remaining spelling issues to review.")
    expect(draft).to_be_focused()


def test_toolbar_toggle_is_remembered_for_other_passages_and_reloads(page, spelled):
    quietly(spelled, install=["en"])
    editor(page, spelled)
    ready(page)
    page.locator("[data-spelling-enable]").uncheck()
    expect(marks(page)).to_have_count(0)
    expect(status(page)).to_contain_text("Local spelling is off")
    page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
    page.locator('#passage-1 [data-action="edit"]').click()
    expect(page.locator("#passage-1 [data-spelling-enable]")).to_be_enabled()
    expect(page.locator("#passage-1 [data-spelling-enable]")).not_to_be_checked()
    page.reload()
    page.locator('#passage-0 [data-action="edit"]').click()
    expect(page.locator("[data-spelling-enable]")).to_be_enabled()
    expect(page.locator("[data-spelling-enable]")).not_to_be_checked()
    page.locator("[data-spelling-enable]").check()
    ready(page)
    assert request(spelled, "GET", "/spelling/settings")["enabled"] is True


def test_auto_download_optout_needs_a_click_and_settings_tab_manages_dictionaries(page, spelled):
    quietly(spelled, auto_download=False)
    calls = []
    page.on("request", lambda req: calls.append(req.url) if "dictionary-tasks" in req.url else 0)
    draft = editor(page, spelled)
    download = page.locator("[data-spelling-download]")
    expect(download).to_be_enabled()
    page.wait_for_timeout(650)
    assert not spelled.downloads and not calls
    download.click()
    ready(page)
    assert len(spelled.downloads) == 3
    expect(download).to_be_hidden()
    settings(page)
    expect(page.locator("#spelling-panel")).to_be_visible()
    expect(page.locator("#processing-panel")).to_be_hidden()
    expect(page.locator("#spelling-auto-download")).not_to_be_checked()
    installed = page.locator("#spelling-installed")
    english = installed.locator('[data-spelling-dictionary="en"]')
    expect(english).to_contain_text("English (United Kingdom, -ise)")
    expect(english).to_contain_text("LicenseRef-SCOWL-2020-12-07")
    expect(english).to_contain_text("Ready on this computer.")
    expect(english.get_by_role("link", name="Licence and attribution ↗")).to_have_attribute(
        "href",
        "https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en-GB/license",
    )
    expect(installed.locator('[data-spelling-dictionary="da"]')).to_contain_text("Not downloaded")
    page.locator("#spelling-enabled").uncheck()
    page.locator("#spelling-settings-save").click()
    expect(page.locator("#spelling-settings-status")).to_have_text("Spelling settings saved.")
    english.locator('[data-spelling-remove="en"]').click()
    expect(english).to_contain_text("Not downloaded")
    close_settings(page)
    expect(draft).to_have_value("😀 hello helllo world")
    page.reload()
    settings(page)
    expect(page.locator("#spelling-enabled")).not_to_be_checked()
    expect(page.locator("#spelling-auto-download")).not_to_be_checked()
    # Local Processing keeps only automatic local processing (user, 20 November).
    page.locator("#tab-processing").click()
    expect(page.locator("#processing-panel")).to_be_visible()
    expect(page.locator("#processing-panel #spelling-enabled")).to_have_count(0)
    assert len(spelled.downloads) == 3


def test_damaged_installed_dictionary_remains_visible_for_explicit_removal(page, spelled):
    quietly(spelled, auto_download=False, install=["en"])
    installed = list(spelled.cache.rglob("dictionary.aff"))
    assert len(installed) == 1
    installed[0].write_bytes(installed[0].read_bytes() + b"\n# synthetic damage\n")
    page.goto(spelled.app.url)
    settings(page)
    english = page.locator('#spelling-installed [data-spelling-dictionary="en"]')
    expect(english).to_contain_text("Dictionary files need attention")
    expect(english.locator('[data-spelling-remove="en"]')).to_be_enabled()
    english.locator('[data-spelling-remove="en"]').click()
    expect(english).to_contain_text("Not downloaded")
    assert not installed[0].exists()
    assert len(spelled.downloads) == 3


def test_failed_download_offers_retry_in_activity_and_waits_for_a_busy_cache(page, spelled):
    quietly(spelled, auto_download=False)
    spelled.control.fail = True
    editor(page, spelled)
    page.locator("[data-spelling-download]").click()
    page.locator("#activity-open").click()
    card = activity_card(page)
    expect(card.locator(".badge")).to_have_text("failed", timeout=10000)
    expect(card).to_contain_text("Check the internet connection")
    expect(card).not_to_contain_text("Synthetic offline")
    expect(page.locator("[data-spelling-download]")).to_be_visible()
    expect(status(page)).to_contain_text("did not finish")
    spelled.control.fail = False
    cache_lock = spelled.service.cache_lock
    assert cache_lock.acquire(timeout=2)
    try:
        card.locator('[data-spelling-task-action="retry"]').click()
        expect(card.locator(".badge")).to_have_text("running", timeout=8000)
        page.wait_for_timeout(500)
        assert not spelled.downloads
    finally:
        cache_lock.release()
    expect(card.locator(".badge")).to_have_text("ready", timeout=10000)
    assert len(spelled.downloads) == 3
    card.locator('[data-spelling-task-action="dismiss"]').click()
    expect(card).to_have_count(0)
    page.locator("#activity-close").click()
    ready(page)


def test_optout_during_a_consented_download_keeps_the_editor_off(page, spelled):
    quietly(spelled, auto_download=False)
    draft = editor(page, spelled)
    before = spelled.store.recording(spelled.rid)
    spelled.gate.clear()
    page.locator("[data-spelling-download]").click()
    expect(status(page)).to_contain_text("Follow it in Activity")
    page.locator("[data-spelling-enable]").uncheck()
    expect(status(page)).to_contain_text("Local spelling is off")
    spelled.gate.set()
    page.wait_for_timeout(2500)
    expect(status(page)).to_contain_text("Local spelling is off")
    expect(page.locator("[data-spelling-enable]")).not_to_be_checked()
    expect(marks(page)).to_have_count(0)
    expect(draft).to_have_value("😀 hello helllo world")
    assert spelled.store.recording(spelled.rid) == before


def test_busy_check_resumes_latest_unchanged_draft_and_stops_on_optout_or_cancel(
    page, spelled, monkeypatch
):
    quietly(spelled, install=["en"])
    draft = editor(page, spelled)
    ready(page)
    before = spelled.store.recording(spelled.rid)
    worker = spelled.service.worker
    exchange = worker._exchange
    entered, released = Event(), Event()
    checks = []

    def slow_first_check(payload, timeout):
        if payload.get("command") == "check" and not entered.is_set():
            entered.set()
            assert released.wait(8), "The test did not release the synthetic cold check."
        return exchange(payload, timeout)

    monkeypatch.setattr(worker, "_exchange", slow_first_check)
    page.on(
        "request",
        lambda req: (
            checks.append(req.post_data_json["text"])
            if req.url.endswith("/spelling/check")
            else None
        ),
    )
    draft.fill("hello oldd")
    deadline = monotonic() + 5
    while not entered.is_set() and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert entered.is_set()
    try:
        draft.fill("hello neww")
        draft.evaluate("node => { window.spellingBusyDraft = node; node.setSelectionRange(7, 7); }")
        expect(status(page)).to_contain_text("Waiting for local spelling")
        released.set()
        ready(page, "neww")
        assert checks[:2] == ["hello oldd", "hello neww"]
        assert checks[-1] == "hello neww" and 3 <= len(checks) <= 5
        expect(draft).to_have_value("hello neww")
        assert draft.evaluate(
            "node => node === window.spellingBusyDraft && "
            "node.selectionStart === 7 && node.selectionEnd === 7"
        )
        count = len(checks)
        page.wait_for_timeout(900)
        assert len(checks) == count, "Ready spelling checks must stop retrying."
    finally:
        released.set()

    assert worker.lock.acquire(timeout=2)
    preference_writes = []

    def hold_optout(route):
        if route.request.method == "PUT" and not route.request.post_data_json["enabled"]:
            preference_writes.append(route)
        else:
            route.continue_()

    page.route("**/api/spelling/settings", hold_optout)
    try:
        draft.fill("hello nextt")
        expect(status(page)).to_contain_text("Waiting for local spelling")
        page.locator("[data-spelling-enable]").uncheck()
        wait_held(page, preference_writes)
        count = len(checks)
        page.wait_for_timeout(900)
        assert len(checks) == count
        preference_writes.pop().continue_()
        expect(status(page)).to_contain_text("Local spelling is off")
        expect(draft).to_have_value("hello nextt")
    finally:
        for route in preference_writes:
            route.continue_()
        page.unroute("**/api/spelling/settings", hold_optout)
        worker.lock.release()

    page.locator("[data-spelling-enable]").check()
    ready(page, "nextt")
    assert worker.lock.acquire(timeout=2)
    try:
        draft.fill("hello otherr")
        expect(status(page)).to_contain_text("Waiting for local spelling")
        page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
        expect(page.locator("[data-spelling-panel]")).to_have_count(0)
        count = len(checks)
        page.wait_for_timeout(900)
        assert len(checks) == count
    finally:
        worker.lock.release()
    assert spelled.store.recording(spelled.rid) == before


@pytest.mark.parametrize("language", ["fr", "da"])
def test_recording_language_controls_support_and_danish_is_check_only(page, spelled, language):
    language_preferences.save(spelled.store, "recording", spelled.rid, language, 0)
    quietly(spelled, install=["da"] if language == "da" else [])
    calls = []
    page.on("request", lambda req: calls.append(req.url))
    editor(page, spelled)
    if language == "fr":
        expect(status(page)).to_contain_text("No spelling dictionary")
        expect(page.locator(".passage-spelling-toggle")).to_have_attribute(
            "title", "No spelling dictionary is available for this recording language."
        )
        expect(marks(page)).to_have_count(0)
        assert not spelled.downloads
    else:
        ready(page)
        ctrl_click(page)
        expect(popover(page)).to_contain_text("This dictionary has no suggestions")
        expect(popover(page).locator("[data-spelling-suggestion]")).to_have_count(0)
        assert len(spelled.downloads) == 3
        assert not any(url.endswith("/spelling/suggest") for url in calls)


@pytest.mark.parametrize("stage", ["check", "suggest", "dictionary-tasks"])
def test_late_spelling_reply_cannot_retarget_a_new_recording(page, spelled, stage):
    quietly(spelled, install=[] if stage == "dictionary-tasks" else ["en"])
    held = []

    def hold(route):
        if held or route.request.method != "POST":
            route.continue_()
        else:
            held.append((route, route.fetch()))

    pattern = (
        "**/spelling/dictionary-tasks"
        if stage == "dictionary-tasks"
        else f"**/recordings/{spelled.rid}/spelling/{stage}"
    )
    page.route(pattern, hold)
    editor(page, spelled)
    if stage == "suggest":
        ready(page)
        mark = marks(page).first.bounding_box()
        box = page.locator("#passage-0 textarea").bounding_box()
        page.locator("#passage-0 textarea").click(
            position={
                "x": mark["x"] + mark["width"] / 2 - box["x"],
                "y": mark["y"] + mark["height"] / 2 - box["y"],
            },
            modifiers=CTRL,
        )
    wait_held(page, held)
    page.evaluate("rid => { location.hash = '/recordings/' + rid; }", spelled.other)
    expect(page.locator("#recording-title")).to_have_text("second")
    page.locator('#passage-0 [data-action="edit"]').click()
    fresh = page.locator('#passage-0 [data-editor-pane="text"] textarea')
    expect(fresh).to_have_value("hello world")
    release(held)
    expect(status(page)).to_have_text("No spelling issues found in this draft.", timeout=15000)
    expect(marks(page)).to_have_count(0)
    expect(popover(page)).to_have_count(0)
    expect(fresh).to_have_value("hello world")


def test_old_check_and_suggestion_cannot_change_new_draft_or_language(page, spelled):
    quietly(spelled, install=["en"])
    draft = editor(page, spelled)
    ready(page)
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route("**/spelling/suggest", hold)
    ctrl_click(page)
    wait_held(page, held)
    draft.fill("hello neww")
    draft.evaluate("node => node.setSelectionRange(4, 4)")
    expect(popover(page)).to_have_count(0)
    release(held)
    ready(page, "neww")
    expect(popover(page)).to_have_count(0)
    expect(draft).to_have_value("hello neww")
    assert draft.evaluate("node => node.selectionStart") == 4
    page.unroute("**/spelling/suggest", hold)
    page.route("**/spelling/check", hold)
    draft.fill("hello draftt")
    wait_held(page, held)
    page.unroute("**/spelling/check", hold)
    page.locator("#file-language").select_option("__choose__")
    page.locator("#language-picker-search").fill("French")
    page.locator("#language-picker-list").select_option("fr")
    page.locator('#language-picker-form button[type="submit"]').click()
    expect(page.locator("#file-language")).to_be_enabled()
    release(held)
    expect(status(page)).to_contain_text("No spelling dictionary")
    expect(marks(page)).to_have_count(0)
    expect(draft).to_have_value("hello draftt")


def test_old_preferences_cannot_overwrite_a_newer_settings_save(page, spelled):
    held = []
    reads = []

    def old_preferences(route):
        if route.request.method == "GET" and not reads:
            reads.append(True)
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route("**/api/spelling/settings", old_preferences)
    editor(page, spelled)
    wait_held(page, held)
    settings(page)
    page.locator("#spelling-enabled").check()
    page.locator("#spelling-auto-download").uncheck()
    page.locator("#spelling-settings-save").click()
    expect(page.locator("#spelling-settings-status")).to_have_text("Spelling settings saved.")
    close_settings(page)
    release(held)
    expect(page.locator("[data-spelling-enable]")).to_be_checked()
    expect(page.locator("[data-spelling-download]")).to_be_enabled()
    assert request(spelled, "GET", "/spelling/settings") == {
        "revision": 1,
        "enabled": True,
        "auto_download": False,
    }
    assert not spelled.downloads


def test_old_suggest_cannot_release_new_draft_action_busy_guard(page, spelled):
    quietly(spelled, install=["en"])
    draft = editor(page, spelled)
    ready(page)
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route("**/spelling/suggest", hold)
    ctrl_click(page)
    wait_held(page, held)
    draft.fill("hello neww otherr")
    ready(page, "neww", "otherr")
    ctrl_click(page, 0)
    wait_held(page, held, 2)
    ignore = popover(page).locator('[data-spelling-action="ignore"]')
    expect(ignore).to_be_disabled()
    release(held)
    page.wait_for_timeout(300)
    expect(ignore).to_be_disabled()
    expect(popover(page).locator(".spelling-popover-word")).to_have_text("neww")
    release(held)
    expect(popover(page).locator("[data-spelling-suggestion]").first).to_be_visible()
    expect(ignore).to_be_enabled()
    page.screenshot(path=str(Path(gettempdir()) / "cbs-spelling-popover-nov20.png"))
    page.keyboard.press("Escape")
    settings(page)
    expect(page.locator("#spelling-installed")).to_contain_text("English")
    page.screenshot(path=str(Path(gettempdir()) / "cbs-spelling-settings-nov20.png"))


@pytest.fixture
def mvp_profile(monkeypatch):
    # Requested before `spelled`, so its app starts under the MVP profile.
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "mvp")


def test_mvp_profile_hides_every_spelling_entry_point_and_requests_nothing(
    page, mvp_profile, spelled
):
    service = spelled.service
    # Existing private data from an earlier full build, with checking switched on.
    service.save_settings(0, True, True)
    service.save_vocabulary(0, ["PrivateName"], spelled.project)
    service.save_vocabulary(0, ["RecordingName"], recording_id=spelled.rid)
    calls = []
    page.on("request", lambda req: calls.append(req.url) if "/spelling/" in req.url else None)
    page.goto(spelled.app.url + f"/#/recordings/{spelled.rid}")
    expect(page.locator("html")).to_have_attribute("data-profile", "mvp")
    page.locator('#passage-0 [data-action="edit"]').click()
    draft = page.locator('#passage-0 [data-editor-pane="text"] textarea')
    expect(draft).to_be_visible()
    draft.fill("hello helllo world")
    page.wait_for_timeout(700)
    expect(page.locator("[data-spelling-panel]")).to_have_count(0)
    expect(page.locator(".passage-spelling-overlay")).to_have_count(0)
    # Static entry points stay hidden even if later code shows them again.
    for selector in (
        "#tab-spelling",
        "#spelling-panel",
        "#spelling-settings",
        "#spelling-project-vocabulary",
        "#spelling-file-project-vocabulary",
        "#spelling-recording-vocabulary",
        "#spelling-vocabulary-dialog",
    ):
        page.evaluate("selector => { document.querySelector(selector).hidden = false; }", selector)
        assert (
            page.evaluate(
                "selector => getComputedStyle(document.querySelector(selector)).display", selector
            )
            == "none"
        )
    page.evaluate(
        "() => { refreshSpellingEditor(true); openSpellingVocabulary('personal'); "
        "openSpellingVocabulary('recording', state.recording.id); showSpellingTab(); "
        "observeSpellingDictionaries([]); }"
    )
    page.evaluate("() => loadSpellingSettings()")
    assert (
        page.evaluate("() => document.querySelector('#spelling-vocabulary-dialog').open") is False
    )
    page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
    page.locator("#settings-open").click()
    page.locator("#tab-processing").click()
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#spelling-settings")).to_be_hidden()
    expect(page.locator("#tab-spelling")).to_be_hidden()
    page.wait_for_timeout(300)
    assert calls == [] and not spelled.downloads and not spelled.cache.exists()
    # The private words remain readable through the API in MVP.
    path = f"/projects/{spelled.project}/spelling/vocabulary"
    assert request(spelled, "GET", path)["words"] == ["PrivateName"]
    words = request(spelled, "GET", f"/recordings/{spelled.rid}/spelling/vocabulary")["words"]
    assert words == ["RecordingName"]


def test_ignore_once_recording_and_project_words_stay_private_with_cas(page, spelled):
    quietly(spelled, install=["en"])
    draft = editor(page, spelled)
    ready(page)
    before = spelled.store.recording(spelled.rid)
    ctrl_click(page)
    popover(page).locator('[data-spelling-action="ignore"]').click()
    expect(marks(page)).to_have_count(0)
    expect(status(page)).to_have_text("No remaining spelling issues to review.")
    assert request(spelled, "GET", "/spelling/vocabulary")["words"] == []
    draft.fill("hello helllo")
    ready(page)
    ctrl_click(page)
    popover(page).locator('[data-spelling-action="project"]').click()
    expect(status(page)).to_have_text("No spelling issues found in this draft.")
    project_path = f"/projects/{spelled.project}/spelling/vocabulary"
    assert request(spelled, "GET", project_path)["words"] == ["helllo"]
    draft.fill("hello neww")
    ready(page, "neww")
    ctrl_click(page)
    popover(page).locator('[data-spelling-action="recording"]').click()
    expect(status(page)).to_have_text("No spelling issues found in this draft.")
    recording_path = f"/recordings/{spelled.rid}/spelling/vocabulary"
    assert request(spelled, "GET", recording_path)["words"] == ["neww"]
    assert request(spelled, "GET", "/spelling/vocabulary")["words"] == []
    # The other recording inherits the project's word but not this recording's.
    assert request(spelled, "GET", f"/recordings/{spelled.other}/spelling/vocabulary") == {
        "revision": 0,
        "words": [],
        "project": {"id": spelled.project, "revision": 1, "words": ["helllo"]},
    }
    page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
    # File Settings offers both the project and the recording vocabulary.
    page.locator("#file-settings-open").click()
    page.locator("#spelling-recording-vocabulary").click()
    dialog = page.locator("#spelling-vocabulary-dialog")
    expect(dialog.locator("#spelling-vocabulary-title")).to_have_text("Recording vocabulary")
    vocab = page.locator("#spelling-vocabulary-text")
    inherited = page.locator("#spelling-vocabulary-project-words")
    expect(vocab).to_have_value("neww")
    expect(inherited).to_have_value("helllo")
    expect(inherited).to_have_attribute("readonly", "")
    vocab.fill("CancelledPrivateName")
    page.locator("#spelling-vocabulary-cancel").click()
    assert request(spelled, "GET", recording_path)["words"] == ["neww"]
    page.locator("#spelling-recording-vocabulary").click()
    expect(vocab).to_have_value("neww")
    vocab.fill("PrivateName")
    current = request(spelled, "GET", recording_path)
    request(spelled, "PUT", recording_path, {"revision": current["revision"], "words": ["Other"]})
    page.locator("#spelling-vocabulary-save").click()
    expect(page.locator("#spelling-vocabulary-status")).to_contain_text("changed")
    expect(vocab).to_have_value("PrivateName")
    assert request(spelled, "GET", recording_path)["words"] == ["Other"]
    page.locator("#spelling-vocabulary-cancel").click()
    page.locator("#spelling-file-project-vocabulary").click()
    expect(dialog.locator("#spelling-vocabulary-title")).to_have_text("Project vocabulary")
    expect(page.locator("#spelling-vocabulary-inherited")).to_be_hidden()
    expect(vocab).to_have_value("helllo")
    vocab.fill("helllo\nSavedName")
    page.locator("#spelling-vocabulary-save").click()
    expect(dialog).to_be_hidden()
    assert request(spelled, "GET", project_path)["words"] == ["helllo", "SavedName"]
    page.locator("#spelling-recording-vocabulary").click()
    expect(inherited).to_have_value("helllo\nSavedName")
    page.locator("#spelling-vocabulary-cancel").click()
    expect(dialog).to_be_hidden()
    assert spelled.store.recording(spelled.rid) == before
    assert all(
        b"PrivateName" not in path.read_bytes() and b"SavedName" not in path.read_bytes()
        for path in spelled.cache.rglob("*")
        if path.is_file()
    )
