"""Default speaker names follow Transcriber's Speaker_01 style without changing saved data."""

from cbs_transcriber.transcripts import (
    default_speaker_names,
    export_transcript,
    normalize_segments,
    resolved_speakers,
)


def passages(*labels):
    return [{"speaker": label, "text": "x"} for label in labels]


def test_raw_labels_from_each_source_get_stable_numbered_names():
    names = default_speaker_names(passages("SPEAKER_01", "SPEAKER_00", "UNKNOWN", "SPEAKER_02"), {})
    assert names == {
        "SPEAKER_01": "Speaker_02",
        "SPEAKER_00": "Speaker_01",
        "UNKNOWN": "Unknown speaker",
        "SPEAKER_02": "Speaker_03",
    }
    assert default_speaker_names(passages("B", "A"), {}) == {
        "B": "Speaker_02",
        "A": "Speaker_01",
    }
    assert default_speaker_names(passages("ENH_ab12_2"), {}) == {"ENH_ab12_2": "Speaker_02"}


def test_names_do_not_depend_on_passage_order():
    first = default_speaker_names(passages("SPEAKER_00", "SPEAKER_01"), {})
    after_delete = default_speaker_names(passages("SPEAKER_01"), {})
    assert after_delete["SPEAKER_01"] == first["SPEAKER_01"] == "Speaker_02"


def test_saved_names_win_and_legacy_enhancement_defaults_are_replaced():
    speakers = {"SPEAKER_00": "Interviewer", "ENH_ab12_1": "Speaker 1"}
    names = default_speaker_names(passages("SPEAKER_00", "ENH_ab12_1"), speakers)
    assert names == {"ENH_ab12_1": "Speaker_01"}
    assert resolved_speakers(passages("SPEAKER_00", "ENH_ab12_1"), speakers) == {
        "SPEAKER_00": "Interviewer",
        "ENH_ab12_1": "Speaker_01",
    }


def test_parts_keep_their_prefix_and_collisions_stay_distinguishable():
    names = default_speaker_names(passages("Part 1 · A", "Part 2 · A"), {})
    assert names == {"Part 1 · A": "Part 1 · Speaker_01", "Part 2 · A": "Part 2 · Speaker_01"}
    # Different label spaces in one result must never appear as one voice.
    mixed = default_speaker_names(passages("A", "SPEAKER_00"), {})
    assert mixed == {"A": "Speaker_01 (A)", "SPEAKER_00": "Speaker_01 (SPEAKER_00)"}
    renamed = default_speaker_names(passages("A", "B"), {"B": "Speaker_01"})
    assert renamed == {"A": "Speaker_01 (A)"}


def test_free_form_labels_are_kept():
    assert default_speaker_names(passages("Interviewer"), {}) == {"Interviewer": "Interviewer"}


def test_exports_use_the_display_names():
    segments = normalize_segments(
        [
            {"text": "Hej.", "start": 0, "end": 1, "speaker": "SPEAKER_00"},
            {"text": "Ja.", "start": 1, "end": 2, "speaker": "SPEAKER_01"},
        ]
    )
    for export_format in ("text", "nvivo-windows", "csv", "srt"):
        content = export_transcript(
            segments, {"SPEAKER_01": "Respondent"}, "speaker", export_format
        )
        assert "Speaker_01" in content and "Respondent" in content
        assert "SPEAKER_00" not in content


def test_recording_and_result_include_defaults_without_changing_saved_names(client):
    store = client.app.state.store
    project = store.create_project("Speakers")
    recording = store.create_recording(project["id"], "a.wav")
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Hej.", "start": 0, "end": 1, "speaker": "SPEAKER_00"}]),
        {},
        "R",
        {},
    )
    current = store.recording(recording["id"])
    assert current["speakers"] == {} and current["speaker_defaults"] == {"SPEAKER_00": "Speaker_01"}
    assert store.result(recording["id"], result["id"])["speaker_defaults"] == {
        "SPEAKER_00": "Speaker_01"
    }
