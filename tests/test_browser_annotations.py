"""Offline prototype evidence coding; all source text and credentials are synthetic."""

import json
import threading
from copy import deepcopy

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.media import ffmpeg
from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser
SOURCE = "😀 Jeg er glad for fleksibiliteten, men hastigheden er dårlig. Jeg er glad."
CODEBOOK = {
    "format": "cbs-topic-codebook-v1",
    "approved": True,
    "topics": [
        {"id": "flexibility", "name": "Fleksibilitet", "definition": "Evaluations of flexibility."},
        {"id": "speed", "name": "Hastighed", "definition": "Evaluations of system speed."},
    ],
}


def seed(local_app, *, codebook=True, run=True):
    store = local_app.server.config.app.state.store
    project = store.create_project("Synthetic coding study")
    recording = store.create_recording(project["id"], "synthetic.wav")
    rid = recording["id"]
    store.execute("UPDATE recordings SET status='ready',duration=4 WHERE id=?", (rid,))
    directory = store.directory(rid)
    directory.mkdir(parents=True)
    ffmpeg(
        ["-f", "lavfi", "-i", "sine=frequency=330:duration=4", str(directory / "audio.mp3")],
        threading.Event(),
    )
    result = store.add_result(
        rid,
        normalize_segments(
            [
                {"text": SOURCE, "start": 1, "end": 3, "speaker": "A"},
                {"text": "Min kollega er uenig. <img src=x onerror=alert(1)>", "speaker": "B"},
            ]
        ),
        {"A": "Research voice A", "B": "Research voice B"},
        "Synthetic transcript",
        {"kind": "import"},
        auto_first=True,
    )
    version = store.result_versions(rid, result["id"])[0]
    book = store.save_codebook(project["id"], CODEBOOK, 0) if codebook else None
    created = (
        store.create_annotation_run(
            rid, result["id"], version["id"], book["id"], request_id="seed-run"
        )
        if run and book
        else None
    )
    return store, project, rid, result, version, book, created


def open_run(page, local_app, rid, run):
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#topics-open").click()
    expect(page.locator("#annotation-fields")).to_be_enabled()
    page.locator("#annotation-run").select_option(run["id"])
    expect(page.locator("#annotation-source-text")).to_have_text(SOURCE)


def bundle_for(result, version, book, *, count=1):
    return {
        "format": "cbs-topic-suggestions-v1",
        "result_id": result["id"],
        "source_version_id": version["id"],
        "source_payload_sha256": version["payload_sha256"],
        "codebook_version_id": book["id"],
        "codebook_payload_sha256": book["payload_sha256"],
        "producer": {"model": "external-declaration", "revision": "unverified-version"},
        "suggestions": [
            {
                "id": f"suggestion-{index}",
                "annotation": {
                    "topic_id": "flexibility",
                    "polarity": "positive",
                    "attribution": "self",
                    "evidence": [
                        {
                            "passage_id": "0",
                            "start_char": SOURCE.index("glad"),
                            "end_char": SOURCE.index("glad") + 4,
                            "exact_text": "glad",
                        }
                    ],
                },
            }
            for index in range(count)
        ],
    }


def import_file(page, value):
    page.locator("#annotation-import-panel > summary").click()
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    page.locator("#annotation-import-file").set_input_files(
        {"name": "suggestions.json", "mimeType": "application/json", "buffer": raw.encode("utf-8")}
    )
    page.locator("#annotation-import").click()


@pytest.mark.parametrize("field", ["name", "definition", "examples"])
def test_codebook_unchanged_save_keeps_literal_fields_and_hash(page, local_app, field):
    store, project, rid, _, _, book, _ = seed(local_app, run=False)
    payload = deepcopy(book["payload"])
    literal = "\nLiteral æ\u0301 🧪\r\n<research wording>\x00"
    payload["topics"][0][field] = [literal, "another example"] if field == "examples" else literal
    book = store.save_codebook(project["id"], payload, book["sequence"])
    original = store.recording(rid)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#topics-open").click()
    expect(page.locator("#codebook-summary")).to_contain_text("version 2")
    expect(page.locator("#annotation-fields")).to_be_enabled()
    if page.locator("#codebook-editor").get_attribute("open") is None:
        page.locator("#codebook-editor > summary").click()
    with page.expect_response(
        lambda response: (
            response.request.method == "POST" and response.url.endswith("/codebook/versions")
        )
    ) as saved:
        page.locator("#codebook-save").click()
    assert saved.value.status == 201
    current = saved.value.json()
    assert current["payload"] == book["payload"]
    assert current["payload_sha256"] == book["payload_sha256"]
    assert current["action"] == "save_no_change"
    assert store.recording(rid) == original
    assert not store.all("SELECT * FROM jobs")


def test_frozen_annotation_source_and_reviewed_quote_render_literal_bytes(page, local_app):
    store, _, rid, result, _, book, _ = seed(local_app, run=False)
    literal = "\nAlpha\r\nBeta\x00 🧪 e\u0301 <literal>"
    before = store.recording(rid)
    saved = store.save_transcript(
        rid,
        normalize_segments([{"text": literal, "speaker": "A"}]),
        {"A": "Synthetic voice"},
        before["revision"],
        result_id=result["id"],
    )
    head = store.result_versions(rid, result["id"])[0]
    run = store.create_annotation_run(
        rid, result["id"], head["id"], book["id"], request_id="literal-source"
    )
    item = store.add_manual_annotation(
        rid,
        run["id"],
        {
            "topic_id": "flexibility",
            "polarity": "positive",
            "attribution": "self",
            "evidence": [
                {
                    "passage_id": "0",
                    "start_char": 0,
                    "end_char": len(literal),
                    "exact_text": literal,
                }
            ],
        },
        request_id="literal-evidence",
    )
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#topics-open").click()
    expect(page.locator("#annotation-fields")).to_be_enabled()
    page.locator("#annotation-run").select_option(run["id"])
    expect(page.locator("#annotation-source-text")).to_have_text(literal)
    assert page.locator("#annotation-source-text").text_content() == literal
    page.locator("#annotation-item").select_option(item["id"])
    expect(page.locator("#annotation-item-status")).to_contain_text("accepted")
    assert page.locator("#annotation-source-text mark").text_content() == literal
    assert (
        page.locator("#annotation-evidence-list p > span").text_content()
        == f"Passage 0 · 0–{len(literal)}: {literal}"
    )
    assert page.locator("#annotation-source-text literal").count() == 0
    assert store.recording(rid) == saved
    assert not store.all("SELECT * FROM jobs")


def test_manual_codebook_unicode_occurrence_and_editor_draft_are_independent(
    page, local_app, tmp_path
):
    store, _, rid, result, _, _, _ = seed(local_app, codebook=False, run=False)
    original = store.recording(rid)
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator("#passage-0 textarea").fill("Unsaved transcript correction")
    page.locator("#topics-open").click()
    expect(page.locator("#annotations-title")).to_contain_text("prototype")
    page.locator('[data-codebook-field="name"]').fill("Fleksibilitet")
    page.locator('[data-codebook-field="definition"]').fill("Attitudes toward flexibility.")
    page.locator("#codebook-approved").check()
    page.locator("#codebook-save").click()
    expect(page.locator("#codebook-summary")).to_have_text("· version 1 · Approved")
    page.locator("#codebook-editor > summary").click()
    page.locator("#annotation-run-new").click()
    expect(page.locator("#annotation-source-text")).to_have_text(SOURCE)
    page.locator("#annotation-quote").fill("glad")
    expect(page.locator("#annotation-occurrence option")).to_have_count(2)
    page.locator("#annotation-occurrence").select_option("1")
    expect(page.locator("#annotation-source-text mark")).to_have_text("glad")
    page.locator("#annotation-add").click()
    expect(page.locator("#annotation-item-status")).to_contain_text("accepted · review revision 1")
    expect(page.locator("#annotation-run option:checked")).to_contain_text("1 annotations")
    run = store.annotation_runs(rid)[0]
    item = store.annotation_item(rid, run["id"], store.annotations(rid, run["id"])[0]["id"])
    span = item["current"]["evidence"][0]
    assert span["start_char"] == SOURCE.rindex("glad")
    assert span["end_char"] == SOURCE.rindex("glad") + 4
    assert item["current"]["polarity"] == "positive"
    for selector in ("#annotation-run-new", "#annotation-seek"):
        bounds = page.locator(selector).bounding_box()
        assert bounds and bounds["height"] < 60
    page.locator("#annotations-title").scroll_into_view_if_needed()
    page.screenshot(path="test-results/topics-manual-evidence.png")
    with page.expect_download() as download:
        page.locator("#annotation-export").click()
    exported = tmp_path / "evidence.json"
    download.value.save_as(exported)
    assert "glad" in exported.read_text(encoding="utf-8")
    page.get_by_role("button", name="Close topic coding", exact=True).click()
    expect(page.locator("#passage-0 textarea")).to_have_value("Unsaved transcript correction")
    expect(page.locator("#result-select")).to_have_value(result["id"])
    assert store.recording(rid) == original


def test_import_review_change_accept_and_reject_keep_original_suggestion(page, local_app):
    store, _, rid, result, version, book, run = seed(local_app)
    open_run(page, local_app, rid, run)
    import_file(page, bundle_for(result, version, book))
    expect(page.locator("#annotation-status")).to_contain_text("unreviewed")
    imported = store.annotation_runs(rid)[0]
    entry = store.annotations(rid, imported["id"])[0]
    page.locator("#annotation-item").select_option(entry["id"])
    expect(page.locator("#annotation-item-status")).to_contain_text(
        "unreviewed · review revision 0"
    )
    page.locator("#annotation-polarity").select_option("negative")
    page.locator('[data-annotation-review="change"]').click()
    expect(page.locator("#annotation-item-status")).to_contain_text("accepted · review revision 1")
    page.locator('[data-annotation-review="accept"]').click()
    expect(page.locator("#annotation-item-status")).to_contain_text("revision 2")
    item = store.annotation_item(rid, imported["id"], entry["id"])
    assert item["suggestion"]["polarity"] == "positive"
    assert item["current"]["polarity"] == "negative"
    page.locator("#annotation-original > summary").click()
    expect(page.locator("#annotation-original-text")).to_contain_text('"polarity": "positive"')
    page.locator('[data-annotation-review="reject"]').click()
    expect(page.locator("#annotation-item-status")).to_contain_text("rejected · review revision 3")
    page.locator("#annotation-passage").select_option("1")
    expect(page.locator("#annotation-source-text")).to_contain_text("<img src=x")
    expect(page.locator("#annotation-source-text img")).to_have_count(0)
    expect(page.locator("#annotation-seek")).to_be_disabled()


def test_duplicate_keys_import_is_rejected_without_browser_normalization(page, local_app):
    store, _, rid, result, version, book, run = seed(local_app)
    open_run(page, local_app, rid, run)
    raw = json.dumps(bundle_for(result, version, book), ensure_ascii=False, separators=(",", ":"))
    raw = raw.replace('"polarity":"positive"', '"polarity":"positive","polarity":"negative"')
    import_file(page, raw)
    expect(page.locator("#annotation-status")).to_have_text(
        "Use a valid suggestion import request."
    )
    assert len(store.annotation_runs(rid)) == 1


def test_annotation_post_commit_refresh_retry_does_not_duplicate_manual_evidence(page, local_app):
    store, _, rid, _, _, _, run = seed(local_app)
    open_run(page, local_app, rid, run)
    fail_once = [True]

    def refresh(route):
        if fail_once[0]:
            fail_once[0] = False
            route.fulfill(status=503, json={"detail": "Refresh failed; retry safely."})
        else:
            route.continue_()

    page.route(f"**/api/recordings/{rid}/annotation-runs/{run['id']}/items?limit=100", refresh)
    page.locator("#annotation-quote").fill("glad")
    page.locator("#annotation-add").click()
    expect(page.locator("#annotation-status")).to_have_text("Refresh failed; retry safely.")
    assert len(store.annotations(rid, run["id"])) == 1
    page.locator("#annotation-add").click()
    expect(page.locator("#annotation-item-status")).to_contain_text("accepted · review revision 1")
    assert len(store.annotations(rid, run["id"])) == 1


def test_annotation_pagination_preserves_draft_and_reviews_an_older_item(page, local_app):
    store, _, rid, result, version, book, _ = seed(local_app)
    imported = store.import_annotation_run(
        rid, bundle_for(result, version, book, count=101), request_id="many-suggestions"
    )
    open_run(page, local_app, rid, imported)
    expect(page.locator("#annotation-item option")).to_have_count(101)
    metadata = store.annotations(rid, imported["id"], limit=100)
    page.locator("#annotation-item").select_option(metadata[0]["id"])
    page.locator("#annotation-polarity").select_option("mixed")
    page.locator("#annotation-items-more").click()
    expect(page.locator("#annotation-item option")).to_have_count(102)
    expect(page.locator("#annotation-polarity")).to_have_value("mixed")
    expect(page.locator("#annotation-item")).to_have_value(metadata[0]["id"])
    oldest = store.annotations(rid, imported["id"], before_sequence=2)[0]
    page.locator("#annotation-item").select_option(oldest["id"])
    expect(page.locator("#annotation-item-status")).to_contain_text("revision 0")
    page.locator('[data-annotation-review="accept"]').click()
    expect(page.locator("#annotation-item-status")).to_contain_text("revision 1")
    expect(page.locator("#annotation-item")).to_have_value(oldest["id"])
    expect(page.locator("#annotation-item option")).to_have_count(102)


def test_frozen_source_staleness_and_late_preview_do_not_replace_newer_drafts(page, local_app):
    store, project, rid, result, _, book, run = seed(local_app)
    payload = bundle_for(result, store.result_versions(rid, result["id"])[0], book)["suggestions"][
        0
    ]["annotation"]
    item = store.add_manual_annotation(rid, run["id"], payload, request_id="one-item")
    current = store.recording(rid)
    changed = current["segments"]
    changed[0]["text"] = "Reviewed later source."
    store.save_transcript(
        rid, changed, current["speakers"], current["revision"], action="free_rewrite"
    )
    updated_book = {**CODEBOOK, "topics": [dict(topic) for topic in CODEBOOK["topics"]]}
    updated_book["topics"][0]["definition"] = "Later coding definition."
    store.save_codebook(project["id"], updated_book, 1)
    current = store.recording(rid)
    other = store.add_result(
        rid,
        normalize_segments([{"text": "Another active result.", "start": 0, "end": 3}]),
        {},
        "Another method",
        {"kind": "import"},
        activate=True,
        revision=current["revision"],
    )
    held = []
    url = f"**/api/recordings/{rid}/annotation-runs/{run['id']}/items/{item['id']}"
    page.route(url, lambda route: held.append(route))
    page.goto(local_app.url + f"/#/recordings/{rid}")
    page.locator("#topics-open").click()
    page.locator("#annotation-run").select_option(run["id"])
    expect(page.locator("#annotation-source-status")).to_contain_text("earlier transcript version")
    expect(page.locator("#annotation-source-status")).to_contain_text("Another saved result")
    expect(page.locator("#annotation-source-status")).to_contain_text("Earlier codebook version")
    expect(page.locator("#annotation-source-text")).to_have_text(SOURCE)
    page.locator("#annotation-item").select_option(item["id"])
    expect(page.locator("#annotation-item")).to_be_disabled()
    page.get_by_role("button", name="Close topic coding", exact=True).click()
    page.locator("#passage-0 [data-action='edit']").click()
    page.locator('#working-edit-dialog [data-working-edit-choice][value="keep"]').click()
    page.locator("#passage-0 textarea").fill("New unsaved transcript draft")
    page.locator("#topics-open").click()
    page.locator("#annotation-run").select_option(run["id"])
    expect(page.locator("#annotation-item")).to_be_enabled()
    with page.expect_response(lambda response: response.url.endswith(f"/items/{item['id']}")):
        held[0].fulfill(json=store.annotation_item(rid, run["id"], item["id"]))
    page.evaluate(
        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
    )
    expect(page.locator("#annotation-review")).to_be_hidden()
    page.get_by_role("button", name="Close topic coding", exact=True).click()
    expect(page.locator("#passage-0 textarea")).to_have_value("New unsaved transcript draft")
    expect(page.locator("#result-select")).to_have_value(other["id"])
