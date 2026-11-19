"""Real spelling UI against tiny pinned dictionaries; no public downloads or private library."""

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
    downloads = []

    def download(entry, target, *, guard=None):
        assert entry["url"] in bodies, "A test tried to download a public dictionary."
        if guard:
            guard()
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
        project=project["id"],
        rid=rid,
        result=result["id"],
        other=other,
        downloads=downloads,
        cache=tmp_path / "cache",
    )
    yield fixture
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


def preferences(fixture, *, enabled=True, auto_download=True):
    current = request(fixture, "GET", "/spelling/settings")
    return request(
        fixture,
        "PUT",
        "/spelling/settings",
        {"revision": current["revision"], "enabled": enabled, "auto_download": auto_download},
    )


def editor(page, fixture, rid=None):
    page.goto(fixture.app.url + f"/#/recordings/{rid or fixture.rid}")
    page.locator('#passage-0 [data-action="edit"]').click()
    expect(page.locator("[data-spelling-panel]")).to_be_visible()
    return page.locator('#passage-0 [data-editor-pane="text"] textarea')


def ready(page):
    expect(page.locator("[data-spelling-issues] summary")).to_have_text(
        "1 possible spelling issue", timeout=15000
    )
    page.locator("[data-spelling-issues]").evaluate("node => { node.open = true; }")


def settings(page):
    page.locator("#settings-open").click()
    page.locator("#tab-processing").click()
    expect(page.locator("#spelling-settings-save")).to_be_enabled()


def close_settings(page):
    page.locator('#settings-dialog [aria-label="Close settings"]').click()
    expect(page.locator("#settings-dialog")).to_be_hidden()


def wait_held(page, held):
    deadline = monotonic() + 8
    while not held and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert held, "The synthetic request was never intercepted."


def release(held):
    route, response = held.pop(0)
    try:
        route.fulfill(response=response)
    except PlaywrightError as error:
        # Text changes intentionally abort in-flight check requests.
        assert "already handled" in str(error) or "Target" in str(error)


def test_default_off_first_use_notice_and_utf16_suggestion_only_change_draft(page, spelled):
    checks, installs = [], []
    held = []

    def check(route):
        checks.append(route.request.post_data_json)
        route.continue_()

    def install(route):
        installs.append(route.request.post_data_json)
        held.append((route, route.fetch()))

    page.route("**/spelling/check", check)
    page.route("**/spelling/dictionary", install)
    before = spelled.store.recording(spelled.rid)
    versions = len(spelled.store.result_versions(spelled.rid, spelled.result))
    draft = editor(page, spelled)
    enable = page.locator("[data-spelling-enable]")
    expect(enable).to_be_enabled()
    expect(enable).not_to_be_checked()
    assert request(spelled, "GET", "/spelling/settings")["auto_download"] is True
    assert not checks and not installs and not spelled.downloads
    assert draft.evaluate("node => node.spellcheck") is False
    assert page.locator("[data-timing-text]").evaluate_all(
        "nodes => nodes.every(node => node.spellcheck === false)"
    )
    enable.check()
    wait_held(page, held)
    expect(page.locator("[data-spelling-status]")).to_contain_text("No transcript text is sent")
    assert set(installs[0]) == {"settings_revision", "consent"}
    assert installs[0]["consent"] is False
    assert len(spelled.downloads) == 3
    release(held)
    ready(page)
    issue = page.locator('[data-spelling-issue="0"]')
    expect(issue.locator("strong")).to_have_text("helllo")
    issue.locator('[data-spelling-action="suggest"]').click()
    suggestion = issue.locator("[data-spelling-suggestion]", has_text="hello").first
    expect(suggestion).to_be_visible(timeout=10000)
    draft.evaluate(
        "node => { window.spellingInputEvents = 0; "
        "node.addEventListener('input', () => window.spellingInputEvents++); }"
    )
    suggestion.click()
    expect(draft).to_have_value("😀 hello hello world")
    assert draft.evaluate("node => node.selectionStart === 14 && node.selectionEnd === 14")
    assert page.evaluate("() => window.spellingInputEvents") == 1
    assert spelled.store.recording(spelled.rid) == before
    assert len(spelled.store.result_versions(spelled.rid, spelled.result)) == versions
    # The chosen suggestion is ordinary input: after the typing pause its draft is checked.
    # Measure the debounce below only once that check has settled (no timing race).
    deadline = monotonic() + 8
    while monotonic() < deadline and not any(
        body["text"] == "😀 hello hello world" for body in checks
    ):
        page.wait_for_timeout(10)
    expect(page.locator("[data-spelling-status]")).to_have_text(
        "No spelling issues found in this draft."
    )
    count = len(checks)
    draft.fill("hello neww")
    page.wait_for_timeout(120)
    draft.fill("hello draftt")
    page.wait_for_timeout(200)
    assert len(checks) == count
    expect(page.locator("[data-spelling-issues] summary")).to_have_text("1 possible spelling issue")
    expect(page.locator('[data-spelling-issue="0"] strong')).to_have_text("draftt")
    assert checks[-1]["text"] == "hello draftt"
    assert all(set(body) == {"text", "request_id"} for body in checks)
    page.locator("[data-editor-mode]").select_option("timing")
    expect(page.locator("[data-spelling-panel]")).to_have_count(0)
    count = len(checks)
    page.wait_for_timeout(600)
    assert len(checks) == count


def test_auto_download_optout_requires_click_and_settings_cache_removal_persists(page, spelled):
    preferences(spelled, auto_download=False)
    draft = editor(page, spelled)
    expect(page.locator("[data-spelling-download]")).to_be_enabled()
    page.wait_for_timeout(650)
    assert not spelled.downloads
    page.locator("[data-spelling-download]").click()
    ready(page)
    assert len(spelled.downloads) == 3
    settings(page)
    expect(page.locator("#spelling-auto-download")).not_to_be_checked()
    installed = page.locator("#spelling-installed")
    expect(installed).to_contain_text("English (United Kingdom, -ise)")
    expect(installed).to_contain_text("LicenseRef-SCOWL-2020-12-07")
    expect(installed.get_by_role("link", name="Licence and attribution ↗")).to_have_attribute(
        "href",
        "https://github.com/wooorm/dictionaries/blob/8cfea406b505e4d7df52d5a19bce525df98c54ab/dictionaries/en-GB/license",
    )
    page.locator("#spelling-enabled").uncheck()
    page.locator("#spelling-settings-save").click()
    expect(page.locator("#spelling-settings-status")).to_have_text("Spelling settings saved.")
    page.locator('[data-spelling-remove="en"]').click()
    expect(installed).to_contain_text("No spelling dictionaries installed")
    close_settings(page)
    expect(draft).to_have_value("😀 hello helllo world")
    page.reload()
    settings(page)
    expect(page.locator("#spelling-enabled")).not_to_be_checked()
    expect(page.locator("#spelling-auto-download")).not_to_be_checked()
    assert len(spelled.downloads) == 3


def test_damaged_installed_dictionary_remains_visible_for_explicit_removal(page, spelled):
    preferences(spelled, auto_download=False)
    editor(page, spelled)
    page.locator("[data-spelling-download]").click()
    ready(page)
    installed = list(spelled.cache.rglob("dictionary.aff"))
    assert len(installed) == 1
    installed[0].write_bytes(installed[0].read_bytes() + b"\n# synthetic damage\n")
    settings(page)
    listing = page.locator("#spelling-installed")
    expect(listing).to_contain_text("English (United Kingdom, -ise)")
    expect(listing).to_contain_text("Dictionary files need attention")
    expect(listing.locator('[data-spelling-remove="en"]')).to_be_enabled()
    listing.locator('[data-spelling-remove="en"]').click()
    expect(listing).to_contain_text("No spelling dictionaries installed")
    assert not installed[0].exists()
    assert len(spelled.downloads) == 3


def test_busy_dictionary_install_keeps_an_explicit_download_retry_usable(page, spelled):
    preferences(spelled, auto_download=False)
    editor(page, spelled)
    expect(page.locator("[data-spelling-download]")).to_be_enabled()
    before = spelled.store.recording(spelled.rid)
    installs = []

    def capture_install(route):
        response = route.fetch()
        installs.append(response.json())
        route.fulfill(response=response)

    page.route("**/spelling/dictionary", capture_install)
    cache_lock = spelled.app.server.config.app.state.spelling.cache_lock
    assert cache_lock.acquire(timeout=2)
    try:
        page.locator("[data-spelling-download]").click()
        wait_held(page, installs)
        assert installs == [{"status": "busy"}]
        expect(page.locator("[data-spelling-download]")).to_be_enabled()
        expect(page.locator("[data-spelling-status]")).to_contain_text("busy")
        page.wait_for_timeout(650)
        assert len(installs) == 1 and not spelled.downloads
    finally:
        cache_lock.release()
    page.locator("[data-spelling-download]").click()
    ready(page)
    assert len(installs) == 2 and len(spelled.downloads) == 3
    assert spelled.store.recording(spelled.rid) == before


def test_late_busy_install_cannot_replace_same_view_optout_status(page, spelled):
    preferences(spelled, auto_download=False)
    draft = editor(page, spelled)
    expect(page.locator("[data-spelling-download]")).to_be_enabled()
    before = spelled.store.recording(spelled.rid)
    held = []

    def hold_busy_install(route):
        response = route.fetch()
        assert response.json() == {"status": "busy"}
        held.append((route, response))

    page.route("**/spelling/dictionary", hold_busy_install)
    cache_lock = spelled.app.server.config.app.state.spelling.cache_lock
    assert cache_lock.acquire(timeout=2)
    try:
        page.locator("[data-spelling-download]").click()
        wait_held(page, held)
    finally:
        cache_lock.release()
    page.locator("[data-spelling-enable]").uncheck()
    expect(page.locator("[data-spelling-status]")).to_contain_text("Local spelling is off")
    release(held)
    page.wait_for_timeout(750)
    expect(page.locator("[data-spelling-status]")).to_contain_text("Local spelling is off")
    expect(page.locator("[data-spelling-enable]")).not_to_be_checked()
    expect(draft).to_have_value("😀 hello helllo world")
    assert not spelled.downloads
    assert spelled.store.recording(spelled.rid) == before


def test_busy_check_resumes_latest_unchanged_draft_and_stops_on_optout_or_cancel(
    page, spelled, monkeypatch
):
    preferences(spelled)
    draft = editor(page, spelled)
    ready(page)
    before = spelled.store.recording(spelled.rid)
    worker = spelled.app.server.config.app.state.spelling.worker
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
        expect(page.locator("[data-spelling-status]")).to_contain_text("Waiting for local spelling")
        released.set()
        expect(page.locator('[data-spelling-issue="0"] strong')).to_have_text("neww", timeout=8000)
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
        expect(page.locator("[data-spelling-status]")).to_contain_text("Waiting for local spelling")
        page.locator("[data-spelling-enable]").uncheck()
        wait_held(page, preference_writes)
        count = len(checks)
        page.wait_for_timeout(900)
        assert len(checks) == count
        preference_writes.pop().continue_()
        expect(page.locator("[data-spelling-status]")).to_contain_text("Local spelling is off")
        expect(draft).to_have_value("hello nextt")
    finally:
        for route in preference_writes:
            route.continue_()
        page.unroute("**/api/spelling/settings", hold_optout)
        worker.lock.release()

    page.locator("[data-spelling-enable]").check()
    ready(page)
    assert worker.lock.acquire(timeout=2)
    try:
        draft.fill("hello otherr")
        expect(page.locator("[data-spelling-status]")).to_contain_text("Waiting for local spelling")
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
    preferences(spelled)
    calls = []
    page.on("request", lambda req: calls.append(req.url))
    editor(page, spelled)
    if language == "fr":
        expect(page.locator("[data-spelling-status]")).to_contain_text("No spelling dictionary")
        assert not spelled.downloads
        expect(page.locator("[data-spelling-issues]")).to_be_hidden()
    else:
        ready(page)
        expect(page.locator("[data-spelling-action=suggest]")).to_have_count(0)
        expect(page.locator('[data-spelling-issue="0"]')).to_contain_text(
            "Correct this word in the passage"
        )
        assert len(spelled.downloads) == 3
        assert not any(url.endswith("/spelling/suggest") for url in calls)


@pytest.mark.parametrize("stage", ["check", "suggest", "dictionary"])
def test_late_spelling_reply_cannot_retarget_a_new_recording(page, spelled, stage):
    preferences(spelled)
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route(f"**/recordings/{spelled.rid}/spelling/{stage}", hold)
    editor(page, spelled)
    if stage == "suggest":
        ready(page)
        page.locator('[data-spelling-action="suggest"]').click()
    wait_held(page, held)
    page.evaluate("rid => { location.hash = '/recordings/' + rid; }", spelled.other)
    expect(page.locator("#recording-title")).to_have_text("second")
    page.locator('#passage-0 [data-action="edit"]').click()
    fresh = page.locator('#passage-0 [data-editor-pane="text"] textarea')
    expect(fresh).to_have_value("hello world")
    release(held)
    expect(page.locator("[data-spelling-status]")).to_have_text(
        "No spelling issues found in this draft.", timeout=15000
    )
    expect(page.locator("[data-spelling-issue]")).to_have_count(0)
    expect(fresh).to_have_value("hello world")


def test_old_check_and_suggestion_cannot_change_new_draft_or_language(page, spelled):
    preferences(spelled)
    draft = editor(page, spelled)
    ready(page)
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route("**/spelling/suggest", hold)
    page.locator('[data-spelling-action="suggest"]').click()
    wait_held(page, held)
    draft.fill("hello neww")
    draft.evaluate("node => node.setSelectionRange(4, 4)")
    release(held)
    expect(page.locator('[data-spelling-issue="0"] strong')).to_have_text("neww")
    expect(page.locator("[data-spelling-suggestion]")).to_have_count(0)
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
    expect(page.locator("[data-spelling-status]")).to_contain_text("No spelling dictionary")
    expect(page.locator("[data-spelling-issue]")).to_have_count(0)
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
    preferences(spelled)
    draft = editor(page, spelled)
    ready(page)
    held = []

    def hold(route):
        held.append((route, route.fetch()))

    page.route("**/spelling/suggest", hold)
    page.locator('[data-spelling-action="suggest"]').click()
    wait_held(page, held)
    draft.fill("hello neww otherr")
    expect(page.locator("[data-spelling-issues] summary")).to_have_text(
        "2 possible spelling issues"
    )
    issue = page.locator('[data-spelling-issue="0"]')
    expect(issue.locator("strong")).to_have_text("neww")
    issue.locator('[data-spelling-action="suggest"]').click()
    deadline = monotonic() + 8
    while len(held) < 2 and monotonic() < deadline:
        page.wait_for_timeout(10)
    assert len(held) == 2
    release(held)
    page.locator('[data-spelling-issue="1"] [data-spelling-action="ignore"]').click()
    expect(issue.locator('[data-spelling-action="suggest"]')).to_be_disabled()
    release(held)
    expect(issue.locator("[data-spelling-suggestion]").first).to_be_visible()
    page.screenshot(path=str(Path(gettempdir()) / "cbs-spelling-issues-nov9.png"))
    settings(page)
    expect(page.locator("#spelling-installed")).to_contain_text("English")
    page.screenshot(path=str(Path(gettempdir()) / "cbs-spelling-settings-nov9.png"))


@pytest.fixture
def mvp_profile(monkeypatch):
    # Requested before `spelled`, so its app starts under the MVP profile.
    monkeypatch.setenv("CBS_TRANSCRIBER_PROFILE", "mvp")


def test_mvp_profile_hides_every_spelling_entry_point_and_requests_nothing(
    page, mvp_profile, spelled
):
    service = spelled.app.server.config.app.state.spelling
    # Existing private data from an earlier full build, with checking switched on.
    service.save_settings(0, True, True)
    service.save_vocabulary(0, ["PrivateName"], spelled.project)
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
    # Static entry points stay hidden even if later code shows them again.
    for selector in (
        "#spelling-settings",
        "#spelling-project-vocabulary",
        "#spelling-vocabulary-dialog",
    ):
        page.evaluate("selector => { document.querySelector(selector).hidden = false; }", selector)
        assert (
            page.evaluate(
                "selector => getComputedStyle(document.querySelector(selector)).display", selector
            )
            == "none"
        )
    page.evaluate("() => { refreshSpellingEditor(true); openSpellingVocabulary('personal'); }")
    page.evaluate("() => loadSpellingSettings()")
    assert (
        page.evaluate("() => document.querySelector('#spelling-vocabulary-dialog').open") is False
    )
    page.locator('#passage-0 [data-editor-pane="text"] [data-action="cancel-edit"]').click()
    page.locator("#settings-open").click()
    page.locator("#tab-processing").click()
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#spelling-settings")).to_be_hidden()
    page.wait_for_timeout(300)
    assert calls == [] and not spelled.downloads and not spelled.cache.exists()
    # The private words remain readable through the API in MVP.
    path = f"/projects/{spelled.project}/spelling/vocabulary"
    assert request(spelled, "GET", path)["words"] == ["PrivateName"]


def test_ignore_once_project_and_personal_words_stay_private_with_vocabulary_cas(page, spelled):
    preferences(spelled)
    draft = editor(page, spelled)
    ready(page)
    before = spelled.store.recording(spelled.rid)
    page.locator('[data-spelling-action="ignore"]').click()
    expect(page.locator("[data-spelling-issue]")).to_have_count(0)
    expect(page.locator("[data-spelling-status]")).to_have_text(
        "No remaining spelling issues to review."
    )
    assert request(spelled, "GET", "/spelling/vocabulary")["words"] == []
    draft.fill("hello helllo")
    ready(page)
    page.locator('[data-spelling-action="project"]').click()
    expect(page.locator("[data-spelling-status]")).to_have_text(
        "No spelling issues found in this draft."
    )
    path = f"/projects/{spelled.project}/spelling/vocabulary"
    assert request(spelled, "GET", path)["words"] == ["helllo"]
    draft.fill("hello neww")
    ready(page)
    page.locator('[data-spelling-action="personal"]').click()
    expect(page.locator("[data-spelling-status]")).to_have_text(
        "No spelling issues found in this draft."
    )
    assert request(spelled, "GET", "/spelling/vocabulary")["words"] == ["neww"]
    page.locator('[data-spelling-vocabulary="project"]').click()
    vocab = page.locator("#spelling-vocabulary-text")
    expect(vocab).to_have_value("helllo")
    vocab.fill("CancelledPrivateName")
    page.locator("#spelling-vocabulary-cancel").click()
    assert request(spelled, "GET", path)["words"] == ["helllo"]
    page.locator('[data-spelling-vocabulary="project"]').click()
    expect(vocab).to_have_value("helllo")
    vocab.fill("PrivateName")
    current = request(spelled, "GET", path)
    request(spelled, "PUT", path, {"revision": current["revision"], "words": ["OtherName"]})
    page.locator("#spelling-vocabulary-save").click()
    expect(page.locator("#spelling-vocabulary-status")).to_contain_text("changed")
    expect(vocab).to_have_value("PrivateName")
    assert request(spelled, "GET", path)["words"] == ["OtherName"]
    page.locator("#spelling-vocabulary-cancel").click()
    page.locator('[data-spelling-vocabulary="personal"]').click()
    expect(vocab).to_have_value("neww")
    vocab.fill("neww\nSavedName")
    page.locator("#spelling-vocabulary-save").click()
    expect(page.locator("#spelling-vocabulary-dialog")).to_be_hidden()
    assert request(spelled, "GET", "/spelling/vocabulary")["words"] == ["neww", "SavedName"]
    page.locator('[data-spelling-vocabulary="personal"]').click()
    expect(vocab).to_have_value("neww\nSavedName")
    expect(page.locator("#spelling-vocabulary-cancel")).to_be_enabled()
    page.locator("#spelling-vocabulary-cancel").click()
    expect(page.locator("#spelling-vocabulary-dialog")).to_be_hidden()
    assert spelled.store.recording(spelled.rid) == before
    assert all(
        b"PrivateName" not in path.read_bytes() and b"SavedName" not in path.read_bytes()
        for path in spelled.cache.rglob("*")
        if path.is_file()
    )
