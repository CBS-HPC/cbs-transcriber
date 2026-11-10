"""Processing replies with generated settings/statuses; no runtime/provider checks."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page
from test_browser_settings_tab_errors import held_settings_load, settings_response

pytestmark = pytest.mark.browser


def open_processing(page, local_app):
    held = held_settings_load(page, local_app, "processing", "enhancement-settings")
    held.fulfill(json=settings_response("processing"))
    page.wait_for_function("() => pendingRequests === 0")
    expect(page.locator("#enhance-device")).to_be_enabled()


def held_requests(page, path, counter):
    held = []

    def hold(route):
        held.append(route)
        page.evaluate("([name, count]) => window[name] = count", [counter, len(held)])

    page.route("**/api/" + path, hold)
    return held


def rules_status(words):
    return {
        "local_minutes": {"words": words, "speakers": 10},
        "cuda_factor": 2,
        "fallback": "stop",
        "rules": {
            "word_minutes": words,
            "speaker_minutes": 10,
            "cuda_factor": 2,
            "fallback": "stop",
        },
        "defaults": {},
    }


@pytest.mark.parametrize("failed", [False, True])
def test_manual_runtime_reply_cannot_restrict_a_newer_interpreter_draft(page, local_app, failed):
    open_processing(page, local_app)
    held = held_requests(page, "enhancement-runtime/check", "manualChecks")
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.manualChecks === 1")
    page.locator("#enhance-python").fill("synthetic-newer-interpreter")
    if failed:
        held[0].fulfill(status=503, json={"detail": "Synthetic older interpreter"})
    else:
        held[0].fulfill(
            json={
                "ready": False,
                "error": "Synthetic older interpreter",
                "gpu": {"checked": True, "available": False},
            }
        )
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#processing-local-status")).not_to_contain_text("older interpreter")
    expect(page.locator("#enhance-python")).to_have_value("synthetic-newer-interpreter")
    expect(page.locator("#provider-error")).to_be_hidden()


def test_automatic_runtime_check_failure_is_visible_and_retryable(page, local_app):
    initial = held_settings_load(
        page, local_app, "processing", "enhancement-settings", local_response={"status": None}
    )
    held = held_requests(page, "enhancement-runtime/check", "automaticChecks")
    initial.fulfill(json=settings_response("processing"))
    page.wait_for_function("() => window.automaticChecks === 1")
    held[0].fulfill(status=503, json={"detail": "Synthetic current runtime check failed"})
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#processing-local-status")).to_contain_text("current runtime check failed")
    expect(page.locator("#settings-dialog")).to_be_visible()
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.automaticChecks === 2")
    held[1].fulfill(json={"ready": True, "version": "synthetic-current", "gpu": {}})
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-current")
    expect(page.locator("#enhance-check")).to_be_enabled()


def test_older_processing_save_does_not_replace_newer_device_choice(page, local_app):
    open_processing(page, local_app)
    held = held_requests(page, "enhancement-settings", "settingsSaves")
    cached = held_requests(page, "local-runtime", "cachedReads")
    page.locator("#enhance-device").select_option("auto")
    page.wait_for_function("() => window.settingsSaves === 1")
    page.locator("#enhance-device").select_option("cuda")
    held[0].fulfill(json=settings_response("processing") | {"device": "auto"})
    page.wait_for_function("() => pendingRequests === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#enhance-device")).to_have_value("cuda")
    page.wait_for_function("() => window.settingsSaves === 2")
    assert held[1].request.post_data_json["device"] == "cuda"
    held[1].fulfill(json=settings_response("processing") | {"device": "cuda"})
    page.wait_for_function("() => window.cachedReads >= 1")
    for route in cached:
        route.fulfill(json={"status": {"ready": False, "error": "Synthetic cache"}})
    expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")


def test_routing_save_reply_preserves_a_newer_unsaved_literal_value(page, local_app):
    open_processing(page, local_app)
    held = held_requests(page, "enhancement-routing", "routingSaves")
    words = page.locator("#rule-word-minutes")
    words.fill("4")
    words.press("Enter")
    page.wait_for_function("() => window.routingSaves === 1")
    words.fill("7")
    held[0].fulfill(json=rules_status(4))
    page.wait_for_function("() => pendingRequests === 0")
    expect(words).to_have_value("7")
    expect(page.locator("#rule-status")).not_to_contain_text("Rules saved")


@pytest.mark.parametrize("section", ["settings", "rules"])
@pytest.mark.parametrize("first_failed", [False, True])
def test_frozen_writes_keep_page_order_and_continue_after_an_error(
    page, local_app, section, first_failed
):
    open_processing(page, local_app)
    path = "enhancement-settings" if section == "settings" else "enhancement-routing"
    held = held_requests(page, path, "orderedWrites")
    page.evaluate(
        """path => {
      const fetchRequest = window.fetch.bind(window);
      window.writeSends = [];
      window.fetch = (url, options) => {
        if (url === '/api/' + path && options?.method === 'PUT')
          window.writeSends.push(JSON.parse(options.body));
        return fetchRequest(url, options);
      };
    }""",
        path,
    )
    if section == "settings":
        page.locator("#enhance-device").select_option("auto")
    else:
        page.locator("#rule-word-minutes").fill("4")
        page.locator("#rule-word-minutes").press("Enter")
    page.wait_for_function("() => window.orderedWrites === 1")
    if section == "settings":
        page.locator("#enhance-device").select_option("cuda")
    else:
        page.locator("#rule-word-minutes").fill("7")
        page.locator("#rule-word-minutes").press("Enter")
    # Two full-setting writes cannot reach the server concurrently and commit in reverse.
    assert len(page.evaluate("window.writeSends")) == 1
    if first_failed:
        held[0].fulfill(status=503, json={"detail": "Synthetic earlier write failed"})
    else:
        held[0].fulfill(
            json=(settings_response("processing") | {"device": "auto"})
            if section == "settings"
            else rules_status(4)
        )
    page.wait_for_function("() => window.orderedWrites === 2")
    payload = held[1].request.post_data_json
    assert payload["device" if section == "settings" else "word_minutes"] == (
        "cuda" if section == "settings" else 7
    )
    held[1].fulfill(
        json=(settings_response("processing") | {"device": "cuda"})
        if section == "settings"
        else rules_status(7)
    )
    page.wait_for_function("() => pendingRequests === 0")
    expect(page.locator("#provider-error")).to_be_hidden()
    if section == "settings":
        expect(page.locator("#enhance-device")).to_have_value("cuda")
        expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    else:
        expect(page.locator("#rule-word-minutes")).to_have_value("7")
        expect(page.locator("#rule-status")).to_contain_text("Rules saved")


def test_reopening_processing_waits_for_an_already_accepted_save(page, local_app):
    open_processing(page, local_app)
    held = []
    committed = settings_response("processing")
    page.evaluate("window.reopenSettingsReads = 0")

    def settings(route):
        if route.request.method == "PUT":
            held.append(route)
            page.evaluate("window.pendingAcceptedSave = true")
        else:
            page.evaluate("window.reopenSettingsReads++")
            route.fulfill(json=committed)

    page.route("**/api/enhancement-settings", settings)
    page.locator("#enhance-device").select_option("auto")
    page.wait_for_function("() => window.pendingAcceptedSave === true")
    page.keyboard.press("Escape")
    page.locator("#settings-open").click()
    page.locator("#tab-processing").click()
    expect(page.locator("#enhance-device")).to_be_disabled()
    assert page.evaluate("window.reopenSettingsReads") == 0
    committed = committed | {"device": "auto"}
    held[0].fulfill(json=committed)
    expect(page.locator("#enhance-device")).to_be_enabled()
    expect(page.locator("#enhance-device")).to_have_value("auto")
    assert page.evaluate("window.reopenSettingsReads") == 1


def watch_json_replies(page, path, counter):
    page.evaluate(
        """([path, counter]) => {
      const fetchRequest = window.fetch.bind(window);
      window[counter] = 0;
      window.fetch = async (...args) => {
        const response = await fetchRequest(...args);
        if (args[0] === '/api/' + path) {
          const read = response.json.bind(response);
          response.json = async () => {
            try { return await read(); }
            finally { window[counter]++; }
          };
        }
        return response;
      };
    }""",
        [path, counter],
    )


def test_older_check_cannot_enable_button_while_newer_interpreter_check_is_pending(page, local_app):
    open_processing(page, local_app)
    held = held_requests(page, "enhancement-runtime/check", "ownedChecks")
    page.route(
        "**/api/enhancement-settings",
        lambda route: route.fulfill(
            json=settings_response("processing") | route.request.post_data_json
        ),
    )
    watch_json_replies(page, "enhancement-runtime/check", "runtimeReads")
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.ownedChecks === 1")
    page.locator("#enhance-python").fill("synthetic-newer-interpreter")
    page.locator("#enhance-python").press("Tab")
    page.wait_for_function("() => window.ownedChecks === 2")
    assert held[1].request.post_data_json["runtime_python"] == "synthetic-newer-interpreter"
    held[0].fulfill(json={"error": "Synthetic old check", "gpu": {}})
    page.wait_for_function("() => window.runtimeReads === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#enhance-check")).to_be_disabled()
    expect(page.locator("#processing-local-status")).not_to_contain_text("old check")
    held[1].fulfill(json={"ready": True, "version": "synthetic-new-check", "gpu": {}})
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-new-check")


def test_old_cloud_check_cannot_enable_a_newly_disconnected_connection(page, local_app):
    checks = held_requests(page, "enhancement-api/check", "cloudChecks")
    loads = []

    def connection(route):
        loads.append(route)
        route.fulfill(
            json=rules_status(15)
            | {"connected": len(loads) == 1, "message": "Synthetic disconnected connection"}
        )

    page.route("**/api/enhancement-api/status", connection)
    page.goto(local_app.url)
    watch_json_replies(page, "enhancement-api/check", "cloudReplies")
    page.locator("#settings-open").click()
    page.locator("#tab-ucloud").click()
    page.wait_for_function("() => window.cloudChecks === 1")
    page.locator("#tab-openai").click()
    page.locator("#tab-ucloud").click()
    expect(page.locator("#enhance-cloud-connection")).to_have_text(
        "Synthetic disconnected connection"
    )
    expect(page.locator("#enhance-cloud-check")).to_be_disabled()
    checks[0].fulfill(json={"prepared": True})
    page.wait_for_function("() => window.cloudReplies === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#enhance-cloud-check")).to_be_disabled()
    expect(page.locator("#enhance-cloud-check-result")).not_to_contain_text("is prepared")


def test_local_check_remains_applicable_after_an_unrelated_rule_edit(page, local_app):
    open_processing(page, local_app)
    checks = held_requests(page, "enhancement-runtime/check", "ruleEditChecks")
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.ruleEditChecks === 1")
    page.locator("#rule-word-minutes").fill("7")
    checks[0].fulfill(json={"ready": True, "version": "synthetic-same-interpreter", "gpu": {}})
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-same-interpreter")
    expect(page.locator("#rule-word-minutes")).to_have_value("7")


def test_cached_reply_from_a_save_does_not_replace_an_active_manual_check(page, local_app):
    open_processing(page, local_app)
    checks = held_requests(page, "enhancement-runtime/check", "activeManualChecks")
    cached = held_requests(page, "local-runtime", "pendingCachedReads")
    page.route(
        "**/api/enhancement-settings",
        lambda route: route.fulfill(
            json=settings_response("processing") | route.request.post_data_json
        ),
    )
    watch_json_replies(page, "local-runtime", "cachedReplies")
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.activeManualChecks === 1")
    page.locator("#enhance-downloads").check()
    page.wait_for_function("() => window.pendingCachedReads === 1")
    cached[0].fulfill(json={"status": {"error": "Synthetic earlier cache", "gpu": {}}})
    page.wait_for_function("() => window.cachedReplies === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#processing-local-status")).to_contain_text("Checking")
    checks[0].fulfill(json={"ready": True, "version": "synthetic-manual-check", "gpu": {}})
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-manual-check")


@pytest.mark.parametrize("section", ["settings", "rules"])
def test_unrelated_draft_does_not_leave_a_finished_write_showing_saving(page, local_app, section):
    open_processing(page, local_app)
    path = "enhancement-settings" if section == "settings" else "enhancement-routing"
    held = held_requests(page, path, "crossStreamWrites")
    if section == "settings":
        page.locator("#enhance-device").select_option("auto")
    else:
        page.locator("#rule-word-minutes").fill("4")
        page.locator("#rule-word-minutes").press("Enter")
    page.wait_for_function("() => window.crossStreamWrites === 1")
    if section == "settings":
        page.locator("#rule-word-minutes").fill("7")
        held[0].fulfill(json=settings_response("processing") | {"device": "auto"})
    else:
        page.locator("#enhance-python").fill("synthetic-unsubmitted-interpreter")
        held[0].fulfill(json=rules_status(4))
    page.wait_for_function("() => pendingRequests === 0")
    if section == "settings":
        expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
        expect(page.locator("#rule-word-minutes")).to_have_value("7")
        expect(page.locator("#rule-status")).to_have_text("Changes not saved.")
    else:
        expect(page.locator("#rule-status")).to_contain_text("Rules saved")
        expect(page.locator("#enhance-python")).to_have_value("synthetic-unsubmitted-interpreter")
        expect(page.locator("#processing-status")).to_have_text("Changes not saved.")


def test_restore_defaults_is_frozen_and_does_not_replace_a_newer_rule_draft(page, local_app):
    open_processing(page, local_app)
    held = held_requests(page, "enhancement-routing", "defaultWrites")
    page.locator("#rule-defaults").evaluate(
        "(button, rules) => button.dataset.defaults = JSON.stringify(rules)",
        rules_status(10)["rules"],
    )
    words = page.locator("#rule-word-minutes")
    words.fill("4")
    words.press("Enter")
    page.wait_for_function("() => window.defaultWrites === 1")
    page.locator("#rule-defaults").click()
    words.fill("7")
    held[0].fulfill(json=rules_status(4))
    page.wait_for_function("() => window.defaultWrites === 2")
    assert held[1].request.post_data_json == rules_status(10)["rules"]
    held[1].fulfill(json=rules_status(10))
    page.wait_for_function("() => pendingRequests === 0")
    expect(words).to_have_value("7")
    expect(page.locator("#rule-status")).to_have_text("Changes not saved.")


def test_cached_read_started_during_a_check_cannot_replace_its_completed_result(page, local_app):
    open_processing(page, local_app)
    checks = held_requests(page, "enhancement-runtime/check", "completedManualChecks")
    cached = held_requests(page, "local-runtime", "olderCachedReads")
    page.route(
        "**/api/enhancement-settings",
        lambda route: route.fulfill(
            json=settings_response("processing") | route.request.post_data_json
        ),
    )
    watch_json_replies(page, "local-runtime", "olderCachedReplies")
    page.locator("#enhance-check").click()
    page.wait_for_function("() => window.completedManualChecks === 1")
    page.locator("#enhance-downloads").check()
    page.wait_for_function("() => window.olderCachedReads === 1")
    checks[0].fulfill(json={"ready": True, "version": "synthetic-fresh-check", "gpu": {}})
    expect(page.locator("#enhance-check")).to_be_enabled()
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-fresh-check")
    cached[0].fulfill(json={"status": {"error": "Synthetic older cache after check", "gpu": {}}})
    page.wait_for_function("() => window.olderCachedReplies === 1")
    page.evaluate("() => new Promise(requestAnimationFrame)")
    expect(page.locator("#processing-local-status")).to_contain_text("synthetic-fresh-check")


@pytest.mark.parametrize("section", ["settings", "rules"])
def test_current_write_failure_keeps_its_values_and_allows_explicit_retry(page, local_app, section):
    open_processing(page, local_app)
    path = "enhancement-settings" if section == "settings" else "enhancement-routing"
    held = held_requests(page, path, "retryWrites")
    if section == "settings":
        page.locator("#enhance-device").select_option("auto")
    else:
        page.locator("#rule-word-minutes").fill("4")
        page.locator("#rule-word-minutes").press("Enter")
    page.wait_for_function("() => window.retryWrites === 1")
    held[0].fulfill(status=503, json={"detail": "Synthetic current write failed"})
    if section == "settings":
        expect(page.locator("#processing-status")).to_contain_text("Save failed")
        expect(page.locator("#enhance-device")).to_have_value("auto")
        page.locator("#enhance-device").select_option("cuda")
    else:
        expect(page.locator("#rule-status")).to_have_text("Synthetic current write failed")
        expect(page.locator("#rule-word-minutes")).to_have_value("4")
        page.locator("#rule-word-minutes").press("Enter")
    page.wait_for_function("() => window.retryWrites === 2")
    held[1].fulfill(
        json=(settings_response("processing") | {"device": "cuda"})
        if section == "settings"
        else rules_status(4)
    )
    page.wait_for_function("() => pendingRequests === 0")
    if section == "settings":
        expect(page.locator("#enhance-device")).to_have_value("cuda")
        expect(page.locator("#processing-status")).to_have_text("Saved for new jobs.")
    else:
        expect(page.locator("#rule-word-minutes")).to_have_value("4")
        expect(page.locator("#rule-status")).to_contain_text("Rules saved")
