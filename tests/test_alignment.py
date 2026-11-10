import copy

from cbs_transcriber.alignment import align_passages, compare_transcripts, phrase_occurrences
from cbs_transcriber.transcripts import normalize_segments


def passages(*texts):
    return normalize_segments([{"text": text} for text in texts])


def test_v24_aligns_split_sentences_and_preserves_both_results():
    left = passages("Hej København. Hvordan går det?", "Det går rigtig godt.")
    right = passages("Hej København.", "Hvordan går det?", "Det går rigtig godt!")
    before = copy.deepcopy((left, right))
    rows = align_passages(left, right)
    assert rows == [
        {"left": ["0"], "right": ["0", "1"], "matched": True},
        {"left": ["1"], "right": ["2"], "matched": True},
    ]
    assert (left, right) == before


def test_v24_insertions_remain_visible_without_shifting_following_matches():
    left = passages("Velkommen til interviewet.", "Hvordan går dit arbejde?", "Tak for din tid.")
    right = passages(
        "Velkommen til interviewet.",
        "Unrelated additional content.",
        "Hvordan går dit arbejde?",
        "Tak for din tid.",
    )
    rows = align_passages(left, right)
    assert {"left": [], "right": ["1"], "matched": False} in rows
    assert rows[-1] == {"left": ["2"], "right": ["3"], "matched": True}
    assert [s for row in rows for s in row["left"]] == ["0", "1", "2"]
    assert [s for row in rows for s in row["right"]] == ["0", "1", "2", "3"]


def test_v24_timing_alone_does_not_establish_a_wording_match():
    left = normalize_segments([{"start": 2, "end": 5, "text": "Ja, det er korrekt."}])
    right = normalize_segments([{"start": 2, "end": 5, "text": "Yes, that's right."}])
    assert not any(row["matched"] for row in align_passages(left, right))
    assert not right[0]["word_timing"]


def test_v24_resynchronizes_after_ten_inserted_passages():
    left = passages("Velkommen til denne samtale.", "Finansiel regulering påvirker forskningen.")
    right = passages(
        "Velkommen til denne samtale.",
        *[f"Unrelated inserted statement {i}." for i in range(10)],
        "Finansiel regulering påvirker forskningen.",
    )
    rows = align_passages(left, right)
    assert rows[-1] == {"left": ["1"], "right": ["11"], "matched": True}
    assert [value for row in rows for value in row["right"]] == [str(i) for i in range(12)]


def test_v24_matches_specific_wording_in_long_passage_middle():
    phrase = "Finansiel regulering påvirker forskningen på CBS"
    left = passages(
        " ".join(["unrelatedleft"] * 200) + " " + phrase + " " + " ".join(["unrelatedleft"] * 200)
    )
    right = passages(phrase)
    assert any(row["matched"] for row in align_passages(left, right))


def test_v24_matches_more_than_three_split_passages():
    right = passages(
        "Velkommen til CBS.",
        "Vi taler om økonomi.",
        "Budgettet er vigtigt.",
        "Forskning tager tid.",
        "Tak for samtalen.",
    )
    left = passages(" ".join(s["text"] for s in right))
    assert align_passages(left, right) == [
        {"left": ["0"], "right": [str(i) for i in range(5)], "matched": True}
    ]


def test_v24_unrelated_text_has_gaps_and_empty_results_work():
    assert align_passages([], []) == []
    rows = align_passages(passages("Completely different."), passages("Intet fælles."))
    assert not any(row["matched"] for row in rows)
    assert any(not row["left"] for row in rows) and any(not row["right"] for row in rows)


def test_v24_alignment_api_rejects_cross_recording_results(client):
    store = client.app.state.store
    project = store.create_project("Compare")
    first = store.create_recording(project["id"], "one.wav")
    second = store.create_recording(project["id"], "two.wav")
    a = store.add_result(first["id"], passages("Hej København."), {}, "A", {})
    b = store.add_result(first["id"], passages("Hej København!"), {}, "B", {})
    c = store.add_result(second["id"], passages("Hej København."), {}, "C", {})
    path = f"/api/recordings/{first['id']}/alignment"
    assert client.get(path, params={"left_id": a["id"], "right_id": b["id"]}).json()["rows"][0][
        "matched"
    ]
    assert client.get(path, params={"left_id": a["id"], "right_id": c["id"]}).status_code == 404


def test_v24_short_distinctive_phrase_and_repeated_fillers():
    left = passages("Unique left prefix Finn Hansson unique left ending.")
    right = passages("Different opening Finn Hansson entirely different closing.")
    result = compare_transcripts(left, right)
    assert result["rows"][0]["matched"] and result["confidence"] == ["partial"]
    spans = result["wording"]["left"]["0"]
    assert any(
        left[0]["text"][s["start"] : s["end"]] == "Finn Hansson" for s in spans if s["equal"]
    )
    fillers = passages("Ja det er.", "Ja det er.", "Ja det er.")
    assert not any(row["matched"] for row in align_passages(fillers, fillers))


def test_v24_explicit_anchor_selects_repeated_wording_without_editing():
    left = passages("Åben diskussion.", "Gentaget frase.", "Gentaget frase.")
    right = passages("ÅBEN DISKUSSION!", "Gentaget frase.", "Gentaget frase.")
    options = phrase_occurrences(right, "gentaget FRASE")
    assert len(options) == 2
    a = phrase_occurrences(left, "gentaget frase")[0]
    b = options[1]
    before = copy.deepcopy((left, right))
    result = compare_transcripts(left, right, (a["index"], b["index"], a["length"]))
    assert {"left": ["1"], "right": ["2"], "matched": True} in result["rows"]
    assert (left, right) == before
    assert phrase_occurrences(left, "ÅBEN diskussion")[0]["passage"] == "0"


def test_v24_phrase_api_rejects_cross_recording_and_invalid_anchor(client):
    store = client.app.state.store
    project = store.create_project("Anchors")
    first = store.create_recording(project["id"], "one.wav")
    other = store.create_recording(project["id"], "two.wav")
    a = store.add_result(first["id"], passages("Finn Hansson"), {}, "A", {})
    b = store.add_result(first["id"], passages("Finn Hansen"), {}, "B", {})
    c = store.add_result(other["id"], passages("Finn Hansson"), {}, "C", {})
    path = f"/api/recordings/{first['id']}/alignment"
    assert (
        client.post(
            path + "/phrases",
            json={
                "left_id": a["id"],
                "right_id": c["id"],
                "phrase": "Finn",
            },
        ).status_code
        == 404
    )
    params = {
        "left_id": a["id"],
        "right_id": b["id"],
        "anchor_left": 0,
        "anchor_right": 0,
        "anchor_length": 2,
    }
    assert client.get(path, params=params).status_code == 400
    params["anchor_length"] = 1
    assert client.get(path, params=params).json()["rows"][0]["matched"]
    del params["anchor_right"]
    assert client.get(path, params=params).status_code == 400
