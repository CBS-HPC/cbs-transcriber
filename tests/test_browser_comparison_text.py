"""Literal transcript rendering, search and free-editor newline preservation (A06)."""

import pytest
from playwright.sync_api import expect
from test_browser import local_app as local_app
from test_browser import page as page

from cbs_transcriber.transcripts import normalize_segments

pytestmark = pytest.mark.browser


def recording(local_app, text, *, timed=False):
    store = local_app.server.config.app.state.store
    project = store.create_project("Literal comparison")
    row = store.create_recording(project["id"], "Synthetic.wav")
    passage = {"text": text}
    if timed:
        tokens = text.split()
        passage.update(
            start=0,
            end=len(tokens),
            words=[
                {"word": word, "start": index, "end": index + 1}
                for index, word in enumerate(tokens)
            ],
        )
    target = store.add_result(row["id"], normalize_segments([passage]), {}, "Target", {})
    source = store.add_result(
        row["id"], normalize_segments([{"text": text + " alternate"}]), {}, "Source", {}
    )
    return store, row["id"], target["id"], source["id"]


def compare(page, source):
    page.locator("#compare-result").select_option(source)
    expect(page.locator("#comparison-info")).to_contain_text("Matching passages aligned")


def edit_passage(page):
    page.locator("#passage-0 [data-action=edit]").click()
    page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
        "button", name="Leave unassigned"
    ).click()


@pytest.mark.parametrize("timed", [False, True])
def test_comparison_retains_literal_crlf_and_exact_search_word_offsets(page, local_app, timed):
    literal = "\nAlpha\r\nBeta gamma 👩‍🔬 æ\u0301"
    store, rid, target, source = recording(local_app, literal, timed=timed)
    page.goto(local_app.url + "/#/recordings/" + rid)
    compare(page, source)
    assert page.locator("#passage-0 .passage-text").text_content() == literal
    assert page.locator("#compare-passage-0 .passage-text").text_content() == literal + " alternate"
    page.evaluate("window.literalPassage = document.querySelector('#passage-0')")
    page.evaluate("window.literalWord = document.querySelectorAll('#passage-0 [data-word]')[1]")
    page.locator("#transcript-search").fill("Beta")
    assert page.locator("#passage-0 mark.search-match").all_text_contents() == ["Beta"]
    assert page.locator("#compare-passage-0 mark.search-match").all_text_contents() == ["Beta"]
    assert page.evaluate("window.literalPassage === document.querySelector('#passage-0')")
    if timed:
        assert page.evaluate(
            "window.literalWord === document.querySelectorAll('#passage-0 [data-word]')[1]"
        )
        assert page.locator("#passage-0 [data-word]").nth(1).text_content() == "Beta"
    page.locator("#transcript-search").fill("👩‍🔬 æ\u0301")
    assert (
        "".join(page.locator("#passage-0 mark.search-match").all_text_contents()) == "👩‍🔬 æ\u0301"
    )
    page.locator("#transcript-search").fill("")
    assert page.locator("#passage-0 .passage-text").text_content() == literal
    assert store.recording(rid)["segments"][0]["text"] == literal
    assert not store.all("SELECT * FROM jobs")


@pytest.mark.parametrize(
    "literal",
    [
        "\nAlpha\r\nBeta gamma",
        "\nAlpha\nBeta gamma",
        "\rAlpha\rBeta gamma",
        "Alpha\x00Beta gamma",
        "\nAlpha\r\nBeta\rgamma\ndelta",
    ],
    ids=["crlf", "leading-lf", "cr", "nul", "mixed"],
)
def test_open_without_edit_disables_save_and_preserves_exact_text_and_timing(
    page, local_app, literal
):
    store, rid, target, source = recording(local_app, literal, timed=True)
    before = store.recording(rid)
    source_before = store.result(rid, source)
    page.goto(local_app.url + "/#/recordings/" + rid)
    assert page.locator("#passage-0 .passage-text").text_content() == literal
    edit_passage(page)
    assert page.locator("#passage-0 textarea").input_value() == literal.replace(
        "\r\n", "\n"
    ).replace("\r", "\n")
    expect(page.locator("#passage-0 [data-action=save-edit]")).to_be_disabled()
    requests = []
    page.on(
        "request", lambda request: requests.append(request) if request.method == "PATCH" else None
    )
    page.locator("#passage-0 [data-action=cancel-edit]:visible").click()
    expect(page.locator("#passage-0 textarea")).to_have_count(0)
    after = store.recording(rid)
    assert after["segments"] == before["segments"]
    assert after["revision"] == before["revision"]
    assert not requests
    assert page.locator("#passage-0 .passage-text").text_content() == literal
    assert store.result(rid, source) == source_before
    assert not store.all("SELECT * FROM jobs")


@pytest.mark.parametrize(
    "text,query,match",
    [
        ("ſolo", "solo", "ſolo"),
        ("İ", "i", None),
        ("Kelvin", "kelvin", "Kelvin"),
        ("KØBENHAVN æøå", "københavn", "KØBENHAVN"),
        ("🧪 [a+b]", "[A+B]", "[a+b]"),
    ],
)
def test_single_and_comparison_search_share_literal_unicode_case_rules(
    page, local_app, text, query, match
):
    store, rid, target, source = recording(local_app, text)
    page.goto(local_app.url + "/#/recordings/" + rid)
    page.locator("#transcript-search").fill(query)
    expected = [] if match is None else [match]
    expect(page.locator("#transcript .passage")).to_have_count(int(bool(expected)))
    assert page.locator("#transcript mark.search-match").all_text_contents() == expected
    compare(page, source)
    expect(page.locator("#transcript .passage")).to_have_count(1)
    assert page.locator("#transcript mark.search-match").all_text_contents() == expected
    assert page.locator("#comparison-transcript mark.search-match").all_text_contents() == expected
    assert page.locator("#passage-0 .passage-text").text_content() == text
    assert store.recording(rid)["segments"][0]["text"] == text


def test_crlf_split_maps_normalized_textarea_cursor_to_original_codepoints(page, local_app):
    literal = "\n🧪 Alpha\r\nBeta gamma"
    store, rid, target, source = recording(local_app, literal, timed=True)
    source_before = store.result(rid, source)
    page.goto(local_app.url + "/#/recordings/" + rid)
    edit_passage(page)
    page.locator("#passage-0 textarea").evaluate(
        "input => { input.selectionStart = input.selectionEnd = input.value.indexOf('Beta'); }"
    )
    with page.expect_response(
        lambda response: response.url.endswith("/segments/0/split-v2")
    ) as split:
        page.locator("#passage-0 [data-action=split]").click()
        page.get_by_role("dialog", name="Which transcript should be Working?").get_by_role(
            "button", name="Leave unassigned"
        ).click()
    assert split.value.status == 200
    expect(page.locator("#transcript .passage")).to_have_count(2)
    passages = store.recording(rid)["segments"]
    assert [passage["text"] for passage in passages] == ["\n🧪 Alpha\r\n", "Beta gamma"]
    assert "".join(passage["text"] for passage in passages) == literal
    assert all(passage["word_timing"] for passage in passages)
    assert store.result(rid, source) == source_before
    assert not store.all("SELECT * FROM jobs")


def test_actual_free_edit_saves_literal_input_and_keeps_originals_and_source(page, local_app):
    literal = "\nAlpha\r\nBeta gamma"
    store, rid, target, source = recording(local_app, literal, timed=True)
    before = store.recording(rid)["segments"][0]
    source_before = store.result(rid, source)
    page.goto(local_app.url + "/#/recordings/" + rid)
    edit_passage(page)
    draft = "\nReviewed\nBeta gamma 👩‍🔬 æ\u0301 <literal>\n"
    page.locator("#passage-0 textarea").fill(draft)
    with page.expect_response(
        lambda response: response.url.endswith("/segments/0/edit-v3")
    ) as saved:
        page.locator("#passage-0 [data-action=save-edit]").click()
    assert saved.value.status == 200
    expect(page.locator("#passage-0 textarea")).to_have_count(0)
    passage = store.recording(rid)["segments"][0]
    assert passage["text"] == draft
    assert page.locator("#passage-0 .passage-text").text_content() == draft
    for field in ("original_text", "original_words", "original_word_timing", "start", "end"):
        assert passage[field] == before[field]
    assert not passage["word_timing"]
    assert store.result(rid, source) == source_before
    assert not store.all("SELECT * FROM jobs")


def test_comparison_search_keeps_exact_draft_and_existing_controls(page, local_app):
    literal = "Alpha\r\nBeta gamma"
    store, rid, target, source = recording(local_app, literal, timed=True)
    before = store.recording(rid)
    page.goto(local_app.url + "/#/recordings/" + rid)
    compare(page, source)
    edit_passage(page)
    draft = "\nUnsent 👩‍🔬 æ\u0301 <literal>\nBeta"
    page.locator("#passage-0 textarea").fill(draft)
    page.evaluate("window.literalDraft = document.querySelector('#passage-0 textarea')")
    page.locator("#transcript-search").fill("Beta")
    assert page.evaluate("window.literalDraft === document.querySelector('#passage-0 textarea')")
    assert page.locator("#passage-0 textarea").input_value() == draft
    assert page.locator("#compare-passage-0 mark.search-match").all_text_contents() == ["Beta"]
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#compare-result").select_option("")
    expect(page.locator("#compare-result")).to_have_value(source)
    assert page.locator("#passage-0 textarea").input_value() == draft
    assert store.recording(rid)["segments"] == before["segments"]
    assert not store.all("SELECT * FROM jobs")
