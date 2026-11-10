"""A02 delayed metadata replies against temporary libraries and the real local UI."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

pytestmark = pytest.mark.browser


def hold(page, pattern, *, commit=False, method=None):
    pending = []

    def capture(route):
        if method is not None and route.request.method != method:
            route.continue_()
            return
        pending.append((route, route.fetch() if commit else None))
        page.evaluate("window.metadataReplyHeld = true")

    page.route(pattern, capture)
    return pending, capture


def project_notes(page):
    page.locator("#project-notes-panel").evaluate("element => element.open = true")
    return page.locator("#project-notes")


def hold_list_observation(page, path, refresh):
    """Pause one real successful API observation before its production UI continuation."""
    page.evaluate(
        """({path, refresh}) => {
      const transport = api;
      let first = true;
      api = (...args) => {
        const observed = transport(...args);
        if (args[0] !== path || !first) return observed;
        first = false;
        return observed.then(value => new Promise(resolve => {
          window.releaseMetadataRead = () => resolve(value);
          window.metadataReadHeld = true;
        }));
      };
      window.olderMetadataRead = refresh === 'refreshProjects'
        ? refreshProjects() : refreshRecordings();
    }""",
        {"path": path, "refresh": refresh},
    )
    page.wait_for_function("() => window.metadataReadHeld === true")


def release_list_observation(page):
    page.evaluate("() => window.releaseMetadataRead()")
    page.evaluate("() => window.olderMetadataRead")


@pytest.mark.parametrize("success", [False, True], ids=["failed-draft", "late-success"])
def test_project_notes_keep_pending_identity_and_newer_observations(page, local_app, success):
    store = local_app.server.config.app.state.store
    first = store.create_project("First metadata project")
    second = store.create_project("Other metadata project")
    pattern = f"**/api/projects/{first['id']}/notes"
    pending, capture = hold(page, pattern, commit=success)
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    draft = "Frozen project draft æøå"
    project_notes(page).fill(draft)
    page.locator("#project-notes-save").click()
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        current = store.one("SELECT * FROM projects WHERE id=?", (first["id"],))
        newer = store.save_notes(
            "projects", first["id"], "Newer externally saved notes", current["notes_revision"]
        )
        page.evaluate("() => refreshProjects()")
        page.locator(f"#projects a[href='#/projects/{second['id']}']").click()
        expect(page.locator("#project-title")).to_have_text(second["name"])
        page.locator(f"#projects a[href='#/projects/{first['id']}']").click()
        expect(page.locator("#project-title")).to_have_text(first["name"])
        route, reply = pending.pop()
        if success:
            route.fulfill(response=reply)
        else:
            route.fulfill(status=503, json={"detail": "Synthetic project note failure"})
        page.unroute(pattern, capture)
        page.wait_for_function("() => savingProjectNotes.size === 0")
        expected = newer["notes"] if success else draft
        expect(project_notes(page)).to_have_value(expected)
        expect(project_notes(page)).to_be_enabled()
        if success:
            project_notes(page).fill("Next accepted project notes")
        url = local_app.url + f"/api/projects/{first['id']}/notes"
        with page.expect_request(url) as sent, page.expect_response(url) as response:
            page.locator("#project-notes-save").click()
        assert sent.value.post_data_json["revision"] == (newer["notes_revision"] if success else 0)
        assert response.value.status == (200 if success else 409)
        if not success:
            expect(project_notes(page)).to_have_value(draft)
            assert (
                store.one("SELECT notes FROM projects WHERE id=?", (first["id"],))["notes"]
                == newer["notes"]
            )
    finally:
        for route, reply in pending:
            if reply is None:
                route.fulfill(status=503, json={"detail": "Synthetic interrupted save"})
            else:
                route.fulfill(response=reply)


@pytest.mark.parametrize("reselect", [False, True], ids=["new-files", "reselected-original"])
def test_collection_move_reply_retains_a_newer_selection_and_exact_count(page, local_app, reselect):
    store = local_app.server.config.app.state.store
    project = store.create_project("Collection move scopes")
    recordings = [
        store.create_recording(project["id"], f"synthetic-{index}.wav") for index in range(3)
    ]
    collection = store.create_collection(project["id"], "Wave")
    pending, _ = hold(page, f"**/api/projects/{project['id']}/collections/move", commit=True)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f"[data-select='{recordings[0]['id']}']").check()
    page.locator("#bulk-collection").select_option(collection["id"])
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        page.locator(f"[data-select='{recordings[0]['id']}']").uncheck()
        expected = recordings[:1] if reselect else recordings[1:]
        for recording in expected:
            page.locator(f"[data-select='{recording['id']}']").check()
        route, reply = pending.pop()
        route.fulfill(response=reply)
        expect(page.locator("#toast")).to_contain_text("Moved 1 recording(s)")
        for recording in expected:
            expect(page.locator(f"[data-select='{recording['id']}']")).to_be_checked()
        assert store.recording(recordings[0]["id"])["collection_id"] == collection["id"]
        assert all(
            store.recording(recording["id"])["collection_id"] is None
            for recording in recordings[1:]
        )
    finally:
        for route, reply in pending:
            route.fulfill(response=reply)


def test_reopened_collection_pending_save_keeps_new_draft_and_controls_recover(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Reopened collection scope")
    collection = store.create_collection(project["id"], "Wave")
    pattern = f"**/api/collections/{collection['id']}"
    pending, capture = hold(page, pattern, commit=True)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f"[data-collection='{collection['id']}']").click()
    page.locator("#collection-settings-open").click()
    page.locator("#collection-name").fill("First accepted rename")
    save = page.locator("#collection-settings-form button[type=submit]")
    save.click()
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        page.locator("#collection-settings-dialog [data-close]").click()
        page.locator("#collection-settings-open").click()
        page.locator("#collection-notes").fill("New draft after reopening")
        expect(save).to_be_disabled()
        route, reply = pending.pop()
        route.fulfill(response=reply)
        page.unroute(pattern, capture)
        expect(save).to_be_enabled()
        expect(page.locator("#collection-notes")).to_have_value("New draft after reopening")
        expect(page.locator("#collection-status")).not_to_have_text("Saved.")
        with page.expect_request(pattern) as request, page.expect_response(pattern) as response:
            save.click()
        assert response.value.status == 200
        assert request.value.post_data_json == {"notes": "New draft after reopening"}
        assert store.collection(collection["id"])["name"] == "First accepted rename"
    finally:
        for route, reply in pending:
            route.fulfill(response=reply)


@pytest.mark.parametrize("action", ["save", "delete"])
def test_collection_old_reply_cannot_change_a_newer_dialog_or_filter(page, local_app, action):
    store = local_app.server.config.app.state.store
    project = store.create_project("Collection dialog scopes")
    first = store.create_collection(project["id"], "First wave")
    second = store.create_collection(project["id"], "Second wave")
    pending, _ = hold(page, f"**/api/collections/{first['id']}", commit=True)
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    page.locator(f"[data-collection='{first['id']}']").click()
    page.locator("#collection-settings-open").click()
    if action == "save":
        page.locator("#collection-name").fill("Renamed first wave")
        page.locator("#collection-settings-form button[type=submit]").click()
    else:
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#collection-delete").click()
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        page.locator("#collection-settings-dialog [data-close]").click()
        page.locator(f"[data-collection='{second['id']}']").click()
        page.locator("#collection-settings-open").click()
        page.locator("#collection-notes").fill("Newer second-wave draft")
        route, reply = pending.pop()
        route.fulfill(response=reply)
        expect(page.locator("#collection-settings-dialog")).to_be_visible()
        expect(page.locator("#collection-notes")).to_have_value("Newer second-wave draft")
        expect(page.locator("#collection-status")).not_to_have_text("Saved.")
        assert (
            page.locator("#collection-settings-form").get_attribute("data-name") == second["name"]
        )
        expect(page.locator(f"[data-collection='{second['id']}']")).to_have_attribute(
            "aria-pressed", "true"
        )
    finally:
        for route, reply in pending:
            route.fulfill(response=reply)


@pytest.mark.parametrize("scope", ["project", "card", "editor", "collection"])
def test_unchanged_literal_notes_do_not_normalize_saved_line_breaks(page, local_app, scope):
    store = local_app.server.config.app.state.store
    project = store.create_project("Literal metadata")
    recording = store.create_recording(project["id"], "synthetic.wav")
    original = "\nFirst æøå\r\nSecond line\rLast line"
    if scope == "collection":
        collection = store.create_collection(project["id"], "Wave")
        store.update_collection(collection["id"], notes=original)
        page.goto(local_app.url + f"/#/projects/{project['id']}")
        page.locator(f"[data-collection='{collection['id']}']").click()
        page.locator("#collection-settings-open").click()
        page.locator("#collection-name").fill("Renamed wave")
        with page.expect_response("**/api/collections/*") as response:
            page.locator("#collection-settings-form button[type=submit]").click()
        assert response.value.status == 200
        assert store.collection(collection["id"])["notes"] == original
        return
    kind, identifier = (
        ("projects", project["id"]) if scope == "project" else ("recordings", recording["id"])
    )
    store.save_notes(kind, identifier, original, 0)
    if scope == "editor":
        page.goto(local_app.url + f"/#/recordings/{recording['id']}")
        page.locator("#editor-layout .description-notes summary").click()
        save = page.locator("#recording-notes-save")
    else:
        page.goto(local_app.url + f"/#/projects/{project['id']}")
        if scope == "project":
            project_notes(page)
            save = page.locator("#project-notes-save")
        else:
            host = page.locator(f"[data-card-notes='{recording['id']}']")
            host.locator("summary").click()
            save = host.locator("[data-save-card-notes]")
    with page.expect_response(f"**/api/{kind}/{identifier}/notes") as response:
        save.click()
    assert response.value.status == 200
    assert store.one(f"SELECT notes FROM {kind} WHERE id=?", (identifier,))["notes"] == original


@pytest.mark.parametrize("scope", ["project", "recording"])
def test_an_older_list_reply_cannot_regress_freshly_saved_notes(page, local_app, scope):
    store = local_app.server.config.app.state.store
    first = store.create_project("Metadata old reads")
    other = store.create_project("Metadata navigation")
    recording = store.create_recording(first["id"], "synthetic.wav")
    page.goto(local_app.url + f"/#/projects/{first['id']}")
    expect(page.locator(".recording-card")).to_have_count(1)
    path = "/projects" if scope == "project" else f"/projects/{first['id']}/recordings"
    refresh = "refreshProjects" if scope == "project" else "refreshRecordings"
    hold_list_observation(page, path, refresh)
    if scope == "project":
        field, save = project_notes(page), page.locator("#project-notes-save")
        kind, identifier = "projects", first["id"]
    else:
        host = page.locator(f"[data-card-notes='{recording['id']}']")
        host.locator("summary").click()
        field, save = host.locator("textarea"), host.locator("[data-save-card-notes]")
        kind, identifier = "recordings", recording["id"]
    field.fill("Newer successful metadata save")
    with page.expect_response(f"**/api/{kind}/{identifier}/notes") as response:
        save.click()
    assert response.value.status == 200
    expect(save).to_be_enabled()
    release_list_observation(page)
    if scope == "project":
        page.locator(f"#projects a[href='#/projects/{other['id']}']").click()
        expect(page.locator("#project-title")).to_have_text(other["name"])
        page.locator(f"#projects a[href='#/projects/{first['id']}']").click()
    expect(field).to_have_value("Newer successful metadata save")
    assert (
        store.one(f"SELECT notes_revision FROM {kind} WHERE id=?", (identifier,))["notes_revision"]
        == 1
    )


@pytest.mark.parametrize("scope", ["project", "recording"])
def test_a_newer_requested_list_cannot_be_replaced_by_an_older_reply(page, local_app, scope):
    store = local_app.server.config.app.state.store
    project = store.create_project("Original project name")
    recording = store.create_recording(project["id"], "Original recording.wav")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    expect(page.locator(".recording-card")).to_have_count(1)
    path = "/projects" if scope == "project" else f"/projects/{project['id']}/recordings"
    refresh = "refreshProjects" if scope == "project" else "refreshRecordings"
    hold_list_observation(page, path, refresh)
    kind, identifier = (
        ("projects", project["id"]) if scope == "project" else ("recordings", recording["id"])
    )
    store.execute(f"UPDATE {kind} SET name=? WHERE id=?", ("Newer requested name", identifier))
    page.evaluate(f"() => {refresh}()")
    release_list_observation(page)
    target = (
        page.locator("#projects")
        if scope == "project"
        else page.locator(f"[data-open='{identifier}']")
    )
    expect(target).to_contain_text("Newer requested name")


def test_pending_recording_rename_owns_its_control_and_ignores_another_view(page, local_app):
    store = local_app.server.config.app.state.store
    project = store.create_project("Recording rename controls")
    first = store.create_recording(project["id"], "First.wav")
    second = store.create_recording(project["id"], "Second.wav")
    page.goto(local_app.url + f"/#/recordings/{first['id']}")
    expect(page.locator("#recording-title")).to_have_text("First")
    pending, _ = hold(page, f"**/api/recordings/{first['id']}", commit=True, method="PATCH")
    page.locator("#recording-rename").click()
    page.locator("#name-input").fill("First accepted rename")
    page.locator("#name-form button[type=submit]").click()
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        expect(page.locator("#recording-rename")).to_be_disabled()
        page.evaluate("hash => location.hash = hash", f"#/recordings/{second['id']}")
        expect(page.locator("#recording-title")).to_have_text("Second")
        expect(page.locator("#recording-rename")).to_be_enabled()
        route, reply = pending.pop()
        route.fulfill(response=reply)
        expect(page.locator("#recording-title")).to_have_text("Second")
    finally:
        for route, reply in pending:
            route.fulfill(response=reply)


def test_late_collection_create_keeps_newer_dialog_and_delete_caption_bound_to_its_id(
    page, local_app
):
    store = local_app.server.config.app.state.store
    project = store.create_project("Collection create scope")
    existing = store.create_collection(project["id"], "Existing wave")
    page.goto(local_app.url + f"/#/projects/{project['id']}")
    pending, _ = hold(
        page, f"**/api/projects/{project['id']}/collections", commit=True, method="POST"
    )
    page.once("dialog", lambda dialog: dialog.accept("New wave"))
    page.locator("#collection-new").click()
    page.wait_for_function("() => window.metadataReplyHeld === true")
    try:
        page.locator(f"[data-collection='{existing['id']}']").click()
        page.locator("#collection-settings-open").click()
        page.locator("#collection-notes").fill("Newer existing-wave draft")
        route, reply = pending.pop()
        created = reply.json()
        route.fulfill(response=reply)
        expect(page.locator(f"[data-collection='{created['id']}']")).to_be_visible()
        selected = page.locator(f"[data-collection='{existing['id']}']").get_attribute(
            "aria-pressed"
        )
        expect(page.locator("#collection-notes")).to_have_value("Newer existing-wave draft")
        captions = []

        def confirm(dialog):
            captions.append(dialog.message)
            dialog.accept()

        page.once("dialog", confirm)
        with (
            page.expect_request("**/api/collections/*") as request,
            page.expect_response("**/api/collections/*") as response,
        ):
            page.locator("#collection-delete").click()
        assert response.value.status == 200
        assert request.value.url.endswith("/" + existing["id"])
        assert '"Existing wave"' in captions[0]
        assert selected == "true"
        assert store.collection(created["id"])["name"] == "New wave"
    finally:
        for route, reply in pending:
            route.fulfill(response=reply)
