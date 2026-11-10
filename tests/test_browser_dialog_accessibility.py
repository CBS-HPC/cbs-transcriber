"""Actual dialog markup/controllers with synthetic replies; no Store/server/network."""

import re
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import page as page

pytestmark = pytest.mark.browser
STATIC = Path(__file__).resolve().parents[1] / "src/cbs_transcriber/static"


def dialog_markup(identifier):
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    return re.search(rf'<dialog\b[^>]*\bid="{identifier}"[^>]*>.*?</dialog>', source, re.S).group()


def expect_focus(page, locator):
    # Another concurrent Chromium run may own window focus; do not refocus the DOM node.
    page.bring_to_front()
    try:
        expect(locator).to_be_focused()
    except AssertionError as error:
        raise AssertionError(
            page.evaluate(
                "({active:document.activeElement.id || document.activeElement.tagName,"
                "foreground:document.hasFocus()})"
            )
        ) from error


DIALOG_NAMES = [
    ("runtime-discard-dialog", "Discard retained run files"),
    ("library-dialog", "Library"),
    ("collection-settings-dialog", "Collection settings"),
    ("file-settings-dialog", "File settings"),
    ("project-settings-dialog", "Project settings"),
    ("delete-project-dialog", "Delete project"),
    ("name-dialog", "New project"),
    ("language-picker-dialog", "Choose a language"),
    ("results-dialog", "Versions"),
    ("result-merge-dialog", "Merge enhancement"),
    ("wording-adoption-dialog", "Use comparison wording"),
    ("history-dialog", "Versions"),
    ("research-report-dialog", "Research report"),
    ("annotations-dialog", "Topics & sentiment (prototype)"),
    ("export-dialog", "Export Settings"),
    ("working-version-dialog", "Mark Working version"),
    ("working-edit-dialog", "Which transcript should be Working?"),
    ("enhancement-dialog", "Add word timing / speakers"),
    ("passage-merge-dialog", "Merge passages"),
    ("speaker-dialog", "Speaker for this passage"),
    ("word-dialog", "Correct a word"),
    ("settings-dialog", "Settings"),
]


def test_static_dialog_title_references_are_unique_and_covered():
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    identifiers = re.findall(r'\bid="([^"]+)"', source)
    dialogs = re.findall(r"<dialog\b([^>]*)>(.*?)</dialog>", source, re.S)
    assert {re.search(r'\bid="([^"]+)"', attrs)[1] for attrs, _ in dialogs} == {
        identifier for identifier, _ in DIALOG_NAMES
    }
    for attrs, body in dialogs:
        title = re.search(r'\baria-labelledby="([^"]+)"', attrs)
        assert title is not None, attrs
        assert identifiers.count(title[1]) == 1, title[1]
        assert re.search(rf'<h2\b[^>]*\bid="{re.escape(title[1])}"', body), title[1]


@pytest.mark.parametrize("identifier,name", DIALOG_NAMES)
def test_native_dialog_names_and_escape_focus(page, identifier, name):
    page.route("**/*", lambda route: route.abort())
    page.set_content('<button id="opener">Open</button>' + dialog_markup(identifier))
    page.evaluate(
        """id => document.querySelector('#opener').onclick = () =>
        document.getElementById(id).showModal()""",
        identifier,
    )
    page.locator("#opener").focus()
    page.keyboard.press("Enter")
    expect(page.get_by_role("dialog", name=name, exact=True)).to_be_visible()
    title = f"{name} · Synthetic æøå 🧪"
    page.get_by_role("heading", name=name, exact=True).evaluate(
        "(node, text) => node.textContent = text", title
    )
    expect(page.get_by_role("dialog", name=title, exact=True)).to_be_visible()
    assert page.locator(f"#{identifier}").evaluate("node => node.contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(page.locator(f"#{identifier}")).not_to_be_visible()
    expect_focus(page, page.locator("#opener"))


@pytest.fixture
def library(page):
    page.set_content(
        '<button id="library-open">Library</button><input id="editor-draft">'
        + dialog_markup("library-dialog")
    )
    page.evaluate("""() => {
      window.pending = []; window.loadErrors = [];
      window.fetch = (path, options = {}) => new Promise((resolve, reject) =>
        pending.push({path, options, resolve, reject}));
      window.api = async (path, options = {}) => {
        const response = await fetch('/api' + path, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail);
        return data;
      };
    }""")
    page.add_script_tag(content=(STATIC / "library.js").read_text(encoding="utf-8"))
    page.evaluate("""() => {
      window.allowedToLeave = true; window.beforeOpens = 0;
      window.chooser = CBSLibraries.bind(document.querySelector('#library-panel'), {
        beforeOpen: () => { beforeOpens += 1; return allowedToLeave; },
      });
      document.querySelector('#library-open').onclick = () => {
        document.querySelector('#library-dialog').showModal();
        chooser.load().catch(error => loadErrors.push(error.message));
      };
    }""")
    page.add_script_tag(content=(STATIC / "portable.js").read_text(encoding="utf-8"))
    page.locator("#library-open").click()
    return page


def library_reply(page, *, path="/api/libraries", data=None, index=-1, error=None):
    page.evaluate(
        """({path, data, index, error}) => {
      const request = pending.filter(item => item.path === path).at(index);
      if (!request) throw new Error('Missing synthetic request');
      if (error) request.resolve({ok: false, status: 409, json: async () => ({detail: error})});
      else request.resolve({ok: true, status: 200, json: async () => data});
    }""",
        {"path": path, "data": data, "index": index, "error": error},
    )


def libraries(label="new", can_switch=True):
    return {
        "can_switch": can_switch,
        "default_new": "/synthetic/default",
        "libraries": [
            {
                "path": f"/synthetic/{label}",
                "exists": True,
                "kind": "Synthetic library",
                "projects": 1,
                "recordings": 0,
            },
            {
                "path": "/synthetic/unreadable",
                "exists": True,
                "kind": "Unreadable fixture",
                "unreadable": True,
            },
        ],
    }


def test_closed_library_dialog_does_not_reload_after_held_switch_response(page):
    page.route("http://fixture.invalid/**", lambda route: route.fulfill(body="navigated"))
    page.goto("http://fixture.invalid/editor")
    page.set_content('<input id="editor-draft">' + dialog_markup("library-dialog"))
    page.evaluate("""() => {
      window.pending = [];
      window.fetch = (path, options = {}) => new Promise((resolve, reject) =>
        pending.push({path, options, resolve, reject}));
    }""")
    page.add_script_tag(content=(STATIC / "library.js").read_text(encoding="utf-8"))
    page.evaluate("""() => {
      window.chooser = CBSLibraries.bind(document.querySelector('#library-panel'));
      document.querySelector('#library-dialog').showModal();
      chooser.load();
    }""")
    library_reply(page, data=libraries())
    page.locator('[data-library-open="/synthetic/new"]').click()
    library_reply(page, path="/api/libraries/open", data={"library": "/synthetic/new"})
    ready_path = "/api/library/switch-ready?expected=%2Fsynthetic%2Fnew"
    page.wait_for_function("path => pending.some(request => request.path === path)", arg=ready_path)
    page.locator("#library-dialog").evaluate("dialog => dialog.close()")
    page.locator("#editor-draft").fill("new unsaved draft")
    library_reply(page, path=ready_path, data={"ready": True})
    page.wait_for_timeout(1000)
    assert page.url == "http://fixture.invalid/editor"
    expect(page.locator("#editor-draft")).to_have_value("new unsaved draft")


def test_failed_library_open_preserves_backup_owned_disabled_states(library):
    library_reply(library, data=libraries())
    library.locator("#portable-create").click()
    assert library.evaluate(
        "pending.some(request => request.path === '/api/library/portable-backups')"
    )
    library.locator('[data-library-open="/synthetic/new"]').click()
    library_reply(library, path="/api/libraries/open", error="Synthetic work is still running.")
    expect(library.locator("[data-library-status]")).to_have_text(
        "Synthetic work is still running."
    )
    expect(library.locator('[data-library-open="/synthetic/new"]')).to_be_enabled()
    expect(library.locator('[data-library-open="/synthetic/unreadable"]')).to_be_disabled()
    for identifier in (
        "portable-create",
        "portable-upload",
        "portable-apply-retention",
        "portable-receipt",
    ):
        expect(library.locator(f"#{identifier}")).to_be_disabled()


@pytest.mark.parametrize("older", ["success", "error"])
def test_library_load_fences_older_reply_and_recomputes_current_eligibility(library, older):
    library.evaluate("() => { chooser.load().catch(error => loadErrors.push(error.message)); }")
    library_reply(library, data=libraries("new", True), index=1)
    if older == "success":
        library_reply(library, data=libraries("old", False), index=0)
    else:
        library_reply(library, error="Earlier synthetic read failed.", index=0)
    expect(library.locator('[data-library-open="/synthetic/new"]')).to_be_enabled()
    expect(library.locator('[data-library-open="/synthetic/old"]')).to_have_count(0)
    expect(library.locator("[data-library-status]")).to_have_text("")
    assert library.evaluate("loadErrors") == []
    library.evaluate("() => { chooser.load().catch(error => loadErrors.push(error.message)); }")
    library_reply(library, data=libraries("blocked", False))
    expect(library.locator("[data-library-new]")).to_be_disabled()
    library.evaluate("() => { chooser.load().catch(error => loadErrors.push(error.message)); }")
    library_reply(library, data=libraries("available", True))
    expect(library.locator("[data-library-new]")).to_be_enabled()
    expect(library.locator("[data-library-browse]")).to_be_enabled()


def test_library_read_failure_is_visible_inside_dialog_and_can_be_retried(library):
    expect(library.locator("[data-library-new]")).to_be_disabled()
    library_reply(library, error="Synthetic library listing failed.")
    expect(library.locator("[data-library-status]")).to_have_text(
        "Synthetic library listing failed."
    )
    expect(library.locator("[data-library-new]")).to_be_disabled()
    library.evaluate("() => { chooser.load().catch(error => loadErrors.push(error.message)); }")
    library_reply(library, data=libraries())
    expect(library.locator("[data-library-new]")).to_be_enabled()


def test_failed_library_open_cannot_reenable_a_newer_unavailable_library(library):
    library_reply(library, data=libraries())
    library.locator('[data-library-open="/synthetic/new"]').click()
    library.evaluate("() => { chooser.load().catch(error => loadErrors.push(error.message)); }")
    library_reply(library, data=libraries("latest", False))
    library_reply(library, path="/api/libraries/open", error="Synthetic opening refused.")
    expect(library.locator("[data-library-status]")).to_have_text("Synthetic opening refused.")
    for selector in ("[data-library-new]", "[data-library-browse]", "[data-library-open]"):
        for button in library.locator(selector).all():
            expect(button).to_be_disabled()


@pytest.mark.parametrize("action", ["browse", "new"])
@pytest.mark.parametrize("reopen", [False, True])
def test_closed_library_ignores_late_unsubmitted_folder_choice(library, action, reopen):
    library_reply(library, data=libraries())
    if action == "new":
        library.once("dialog", lambda dialog: dialog.dismiss())
    library.locator(f"[data-library-{action}]").click()
    assert library.evaluate("pending.some(request => request.path === '/api/storage/browse')")
    library.keyboard.press("Escape")
    expect(library.locator("#library-dialog")).not_to_be_visible()
    library.locator("#editor-draft").fill("A newer synthetic unsaved draft.")
    library.evaluate("allowedToLeave = false")
    if reopen:
        library.locator("#library-open").click()
        library_reply(library, data=libraries("reopened"))
    library_reply(library, path="/api/storage/browse", data={"path": "/synthetic/late-choice"})
    library.evaluate("() => new Promise(requestAnimationFrame)")
    assert (
        library.evaluate("pending.filter(request => request.path === '/api/libraries/open').length")
        == 0
    )
    assert library.evaluate("beforeOpens") == 1
    expect(library.locator("#editor-draft")).to_have_value("A newer synthetic unsaved draft.")


@pytest.fixture
def conflicts(page):
    page.set_content("""<dialog id="results-dialog">
      <button data-conflict-review="incoming">Review conflicts</button></dialog>
      <p id="save-status"></p><div id="transcript"></div>""")
    page.evaluate("""() => {
      window.$ = selector => document.querySelector(selector);
      window.h = value => String(value).replace(/[&<>"']/g,
        char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      window.state = {recording: {id:'recording', active_transcript_id:'ours', revision:1}};
      window.viewEpoch = 1; window.pendingResultMutations = new Set();
      window.recordingContext = () => ({id:'recording', result:'ours', epoch:1});
      window.sameRecordingView = context => context.id === state.recording.id;
      window.resultsContext = {...recordingContext(), recording: {transcripts:[
        {id:'incoming', label:'Synthetic incoming',
          source:{kind:'enhancement',source_result_id:'ours'}}]}};
      window.currentResults = context => context === resultsContext;
      window.pending = [];
      window.api = (path, options={}) => new Promise((resolve,reject) =>
        pending.push({path,options,resolve,reject}));
      window.canDiscardPassage = () => true;
      window.renderResults = () => {}; window.renderManagedResults = () => {};
      document.querySelector('#results-dialog').showModal();
    }""")
    page.add_script_tag(content=(STATIC / "conflict_review.js").read_text(encoding="utf-8"))
    page.locator("[data-conflict-review]").click()
    conflict_reply(page, "/recordings/recording", {"active_transcript_id": "ours", "revision": 1})
    conflict_reply(page, "/conflicts-preview", preview())
    expect(page.locator("#conflict-review-refresh")).to_be_enabled()
    return page


def preview(choice=None):
    return {
        "result_id": "ours",
        "incoming_result_id": "incoming",
        "revision": 1,
        "fingerprint": "a" * 64,
        "incoming_label": "Synthetic incoming",
        "decisions": []
        if choice is None
        else [{"passage_id": "passage", "group": "wording", "choice": choice}],
        "summary": {
            "decisions": int(choice is not None),
            "current": int(choice == "current"),
            "incoming": int(choice == "incoming"),
            "changed_passages": 0,
        },
        "passages": [
            {
                "id": "passage",
                "ordinal": 1,
                "groups": {
                    "wording": {
                        "base": "Base",
                        "current": "Current",
                        "incoming": "Incoming",
                        "incoming_allowed": True,
                    }
                },
            }
        ],
    }


def conflict_reply(page, suffix, value=None, error=None):
    page.evaluate(
        """({suffix,value,error}) => {
      const request = pending.find(item => !item.settled && item.path.endsWith(suffix));
      if (!request) throw new Error('Missing synthetic request: '+suffix);
      request.settled = true;
      if (error) request.reject(new Error(error)); else request.resolve(value);
    }""",
        {"suffix": suffix, "value": value, "error": error},
    )


@pytest.mark.parametrize("outcome", ["success", "error", "user-moved-focus"])
def test_conflict_keyboard_choice_survives_preview_without_stealing_newer_focus(conflicts, outcome):
    choice = conflicts.locator("[data-conflict-choice]")
    choice.focus()
    choice.press("ArrowDown")
    expect_focus(conflicts, conflicts.locator("#conflict-review-status"))
    conflict_reply(
        conflicts, "/recordings/recording", {"active_transcript_id": "ours", "revision": 1}
    )
    if outcome == "user-moved-focus":
        conflicts.get_by_role("button", name="Close conflict review").focus()
    conflict_reply(
        conflicts,
        "/conflicts-preview",
        preview("current"),
        error="Synthetic preview conflict; refresh." if outcome == "error" else None,
    )
    expect(conflicts.locator("#conflict-review-refresh")).to_be_enabled()
    if outcome == "user-moved-focus":
        expect_focus(conflicts, conflicts.get_by_role("button", name="Close conflict review"))
    else:
        expect_focus(conflicts, choice)
    expect(choice).to_have_value("current")
    if outcome == "error":
        expect(conflicts.locator("#conflict-review-error")).to_have_text(
            "Synthetic preview conflict; refresh."
        )
        expect(conflicts.locator("#conflict-review-apply")).to_be_disabled()


def test_conflict_apply_error_returns_keyboard_to_refresh(conflicts):
    conflicts.locator("[data-conflict-choice]").select_option("current")
    conflict_reply(
        conflicts, "/recordings/recording", {"active_transcript_id": "ours", "revision": 1}
    )
    conflict_reply(conflicts, "/conflicts-preview", preview("current"))
    expect(conflicts.locator("#conflict-review-apply")).to_be_enabled()
    conflicts.locator("#conflict-review-apply").focus()
    expect_focus(conflicts, conflicts.locator("#conflict-review-apply"))
    conflicts.keyboard.press("Enter")
    expect_focus(conflicts, conflicts.locator("#conflict-review-status"))
    conflict_reply(conflicts, "/conflicts", error="Synthetic stale review; refresh.")
    expect(conflicts.locator("#conflict-review-error")).to_have_text(
        "Synthetic stale review; refresh."
    )
    expect(conflicts.locator("#conflict-review-refresh")).to_be_enabled()
    expect(conflicts.locator("#conflict-review-apply")).to_be_disabled()
    expect_focus(conflicts, conflicts.locator("#conflict-review-refresh"))
