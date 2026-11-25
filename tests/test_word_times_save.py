"""Save passage with typed word times as one version (ADR 0195). Synthetic data only."""

from copy import deepcopy

import pytest
from test_change_undo import ok, step
from test_free_text_timing_retention import passage

from cbs_transcriber.transcripts import normalize_segments
from cbs_transcriber.word_times import edit_text_and_word_times


def seed(client, text="Hej København igen.", *, crlf=False, untimed=None):
    store = client.app.state.store
    project = store.create_project("Synthetic word times")
    recording = store.create_recording(project["id"], "synthetic.wav")
    segment = passage(text)
    if crlf:
        segment = normalize_segments(
            [
                {
                    "text": "Hej\r\nKøbenhavn igen.",
                    "start": 0,
                    "end": 4,
                    "speaker": "A",
                    "words": [
                        {"word": "Hej", "start": 0, "end": 0.75},
                        {"word": "København", "start": 1, "end": 1.75},
                        {"word": "igen.", "start": 2, "end": 2.75},
                    ],
                }
            ]
        )[0]
    if untimed is not None:
        segment["words"][untimed].update(start=None, end=None)
        segment["word_timing"] = False
    other = passage("Unrelated passage.")
    other["id"] = "other"
    store.add_result(
        recording["id"], [segment, other], {"A": "Voice"}, "Synthetic", {}, auto_first=True
    )
    return store, store.recording(recording["id"])


def save(client, current, text, word_times, segment_id="0"):
    return client.patch(
        f"/api/recordings/{current['id']}/segments/{segment_id}/edit-v4",
        json={
            "revision": current["revision"],
            "mode": "free",
            "text": text,
            "word_times": word_times,
        },
    )


def typed(text, word, start, end, occurrence=0):
    characters = list(text)
    index = -1
    for _ in range(occurrence + 1):
        index = "".join(characters).index(word, index + 1)
    left = len(list(text[:index]))
    return {
        "char_start": left,
        "char_end": left + len(list(word)),
        "word": word,
        "start": start,
        "end": end,
    }


def versions(store, current):
    return store.result_versions(current["id"], current["active_transcript_id"])


def test_times_only_save_is_one_word_timing_version_and_keeps_everything_else(client):
    store, current = seed(client)
    before = deepcopy(current["segments"])
    text = before[0]["text"]
    response = save(client, current, text, [typed(text, "København", 1.1, 1.6)])
    saved = ok(response)
    words = saved["segments"][0]["words"]
    assert saved["segments"][0]["text"] == text
    assert (words[1]["start"], words[1]["end"], words[1]["manual_timing"]) == (1.1, 1.6, True)
    assert words[0] == before[0]["words"][0] and words[2] == before[0]["words"][2]
    assert saved["segments"][0]["original_words"] == before[0]["original_words"]
    assert saved["segments"][1] == before[1]
    assert saved["segments"][0]["word_timing"] is True
    history = versions(store, current)
    assert len(history) == 2
    assert history[0]["action"] == "word_timing_edit"
    context = history[0]["context"]
    assert (context["segment_id"], context["mode"], context["word_index"]) == ("0", "timed_word", 1)
    assert "text_policy" not in context
    assert saved["result_roles"]["working"]["version_id"] == history[0]["id"]


def test_text_and_times_are_one_version_and_undo_restores_both(client):
    store, current = seed(client)
    before = deepcopy(current["segments"])
    text = "Hej smukke København igen."
    response = save(
        client,
        current,
        text,
        [typed(text, "smukke", 0.8, 0.95), typed(text, "København", 1.05, 1.7)],
    )
    saved = ok(response)
    words = saved["segments"][0]["words"]
    assert [word["word"] for word in words] == ["Hej", "smukke", "København", "igen."]
    assert (words[1]["start"], words[1]["end"], words[1]["manual_timing"]) == (0.8, 0.95, True)
    assert (words[2]["start"], words[2]["end"], words[2]["manual_timing"]) == (1.05, 1.7, True)
    assert words[0] == before[0]["words"][0]
    assert words[3] == before[0]["words"][2] | {"char_start": 21, "char_end": 26}
    # Every word is timed now, so the passage claims complete timing and no warning remains.
    assert saved["segments"][0]["word_timing"] is True
    assert "timing_retention_warning" not in saved
    history = versions(store, current)
    assert len(history) == 2
    assert history[0]["action"] == "free_rewrite"
    context = history[0]["context"]
    assert (context["mode"], context["text_policy"]) == ("free", "permissive-word-map-v2")
    undone = ok(step(client, saved))
    assert undone["segments"] == before


def test_untyped_new_word_stays_untimed_with_the_usual_warning(client):
    store, current = seed(client)
    text = "Hej smukke København igen."
    saved = ok(save(client, current, text, [typed(text, "København", 1.05, 1.7)]))
    words = saved["segments"][0]["words"]
    assert (words[1]["word"], words[1]["start"], words[1]["end"]) == ("smukke", None, None)
    assert saved["segments"][0]["word_timing"] is False
    assert "no verified timing" in saved["timing_retention_warning"]


def test_order_of_typed_edits_does_not_matter(client):
    store, current = seed(client, "en to tre")
    text = "en to tre"
    # Moving both words later is valid only once both moves are applied.
    saved = ok(
        save(client, current, text, [typed(text, "en", 1.0, 1.8), typed(text, "to", 2.0, 2.6)])
    )
    words = saved["segments"][0]["words"]
    assert [(word["start"], word["end"]) for word in words] == [(1.0, 1.8), (2.0, 2.6), (2, 2.75)]
    assert "word_index" not in versions(store, current)[0]["context"]


@pytest.mark.parametrize(
    "word_time, message",
    [
        ({"start": 1.5, "end": 1.2}, "start before end"),
        ({"start": 1.5, "end": 1.5}, "start before end"),
        ({"start": 1.0, "end": 9.0}, "within the passage"),
        ({"start": 2.5, "end": 3.0}, "passage order"),
        ({"start": 0.0, "end": 0.5}, "passage order"),
    ],
)
def test_invalid_times_are_refused_without_a_version(client, word_time, message):
    store, current = seed(client)
    text = current["segments"][0]["text"]
    response = save(client, current, text, [typed(text, "København", 0, 0) | word_time])
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert len(versions(store, current)) == 1
    assert store.recording(current["id"])["segments"] == current["segments"]


@pytest.mark.parametrize(
    "entries",
    [
        # The named range no longer holds that word.
        [{"char_start": 0, "char_end": 3, "word": "Hej!", "start": 0.1, "end": 0.5}],
        [{"char_start": 0, "char_end": 40, "word": "Hej", "start": 0.1, "end": 0.5}],
        # One range spanning two words, and two ranges for one word.
        [{"char_start": 0, "char_end": 13, "word": "Hej København", "start": 0, "end": 1}],
        [
            {"char_start": 4, "char_end": 7, "word": "Køb", "start": 1.0, "end": 1.2},
            {"char_start": 7, "char_end": 13, "word": "enhavn", "start": 1.3, "end": 1.6},
        ],
        # Overlapping typed ranges.
        [
            {"char_start": 0, "char_end": 3, "word": "Hej", "start": 0.1, "end": 0.5},
            {"char_start": 2, "char_end": 3, "word": "j", "start": 0.6, "end": 0.7},
        ],
    ],
)
def test_ranges_that_do_not_name_exactly_one_word_are_refused(client, entries):
    store, current = seed(client)
    response = save(client, current, current["segments"][0]["text"], entries)
    assert response.status_code == 400
    assert "no longer matches one word" in response.json()["detail"]
    assert len(versions(store, current)) == 1


def test_untimed_word_gets_only_the_typed_time_and_no_change_is_refused(client):
    store, current = seed(client, untimed=1)
    text = current["segments"][0]["text"]
    same = save(client, current, text, [typed(text, "Hej", 0, 0.75)])
    assert same.status_code == 400 and "no changes" in same.json()["detail"]
    saved = ok(save(client, current, text, [typed(text, "København", 1.2, 1.5)]))
    words = saved["segments"][0]["words"]
    assert (words[1]["start"], words[1]["end"], words[1]["manual_timing"]) == (1.2, 1.5, True)
    # A times-only save keeps the passage's own completeness claim, as Edit word did.
    assert saved["segments"][0]["word_timing"] is False
    assert len(versions(store, current)) == 2


def test_passage_without_bounds_refuses_typed_times(client):
    store, current = seed(client)
    segments = deepcopy(current["segments"])
    segments[0].update(start=None, end=None)
    current = store.save_transcript(
        current["id"], segments, current["speakers"], current["revision"], action="manual_save"
    )
    text = segments[0]["text"]
    response = save(client, current, text, [typed(text, "Hej", 0.1, 0.5)])
    assert response.status_code == 400
    assert "audio bounds" in response.json()["detail"]


def test_crlf_and_emoji_ranges_are_code_points_of_the_saved_text(client):
    store, current = seed(client, crlf=True)
    text = current["segments"][0]["text"]
    assert "\r\n" in text
    saved = ok(save(client, current, text, [typed(text, "København", 1.2, 1.4)]))
    assert saved["segments"][0]["text"] == text
    assert saved["segments"][0]["words"][1]["start"] == 1.2
    store, current = seed(client, "Hej 👍🏽 igen")
    text = "Hej 👍🏽 hele igen"
    saved = ok(save(client, current, text, [typed(text, "hele", 1.8, 1.9)]))
    words = saved["segments"][0]["words"]
    assert [word["word"] for word in words] == ["Hej", "👍🏽", "hele", "igen"]
    assert (words[2]["char_start"], words[2]["char_end"]) == (7, 11)
    assert (words[2]["start"], words[2]["end"]) == (1.8, 1.9)


def test_stale_revision_and_extra_fields_are_refused(client):
    store, current = seed(client)
    text = current["segments"][0]["text"]
    ok(save(client, current, text, [typed(text, "Hej", 0.1, 0.5)]))
    stale = save(client, current, text, [typed(text, "Hej", 0.2, 0.5)])
    assert stale.status_code in (400, 409)
    extra = client.patch(
        f"/api/recordings/{current['id']}/segments/0/edit-v4",
        json={
            "revision": store.recording(current["id"])["revision"],
            "mode": "free",
            "text": text,
            "word_times": [typed(text, "Hej", 0.2, 0.5) | {"realign": True}],
        },
    )
    assert extra.status_code == 422
    empty = client.patch(
        f"/api/recordings/{current['id']}/segments/0/edit-v4",
        json={"revision": 0, "mode": "free", "text": text, "word_times": []},
    )
    assert empty.status_code == 422
    assert len(versions(store, current)) == 2


def test_times_only_save_cannot_reactivate_a_stale_legacy_map(client):
    store, current = seed(client)
    segments = deepcopy(current["segments"])
    segments[0].update(edited=True, word_timing=False, words_current=False)
    current = store.save_transcript(
        current["id"], segments, current["speakers"], current["revision"], action="manual_save"
    )
    text = segments[0]["text"]
    before = versions(store, current)
    response = save(client, current, text, [typed(text, "Hej", 0.1, 0.5)])
    assert response.status_code == 400
    assert "saved word mapping is stale" in response.json()["detail"]
    assert store.recording(current["id"])["segments"] == current["segments"]
    assert versions(store, current) == before


def test_pure_helper_never_mutates_its_input_or_times_unnamed_words():
    original = passage("en to tre")
    before = deepcopy(original)
    edited, warning, changed_text, changed = edit_text_and_word_times(
        original,
        "en fire to tre",
        [{"char_start": 3, "char_end": 7, "word": "fire", "start": 0.8, "end": 0.9}],
    )
    assert original == before
    assert changed_text is True and changed == [1]
    assert [(word["word"], word["start"]) for word in edited["words"]] == [
        ("en", 0),
        ("fire", 0.8),
        ("to", 1),
        ("tre", 2),
    ]
    assert warning is None
