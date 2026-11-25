"""Passage history steps use saved versions and preserve the rest of the active result."""

from test_change_undo import content, edit, ok, step, structure
from test_change_undo import row as row


def path(row, index=0):
    return (
        f"/api/recordings/{row['id']}/transcripts/{row['active_transcript_id']}"
        f"/segments/{row['segments'][index]['id']}/change-steps"
    )


def options(client, row, index=0):
    return ok(client.get(path(row, index)))


def apply(client, row, kind, offered=None, index=0):
    offered = offered or options(client, row, index)
    return client.post(
        f"{path(row, index)}/{kind}",
        json={
            "revision": offered["revision"],
            "head_version_id": offered["head_version_id"],
            "version_id": offered[kind]["version_id"],
        },
    )


def test_passage_steps_walk_both_ways_and_preserve_other_edits(client, row):
    first = edit(client, row, 0, "Alpha first edit")
    other = edit(client, first, 1, "Beta edited")
    last = edit(client, other, 0, "Alpha second edit")
    assert options(client, last)["undo"]["action"] == "free_rewrite"
    once = ok(apply(client, last, "undo"))
    assert once["segments"][0] == first["segments"][0]
    twice = ok(apply(client, once, "undo"))
    assert twice["segments"][0] == row["segments"][0]
    assert twice["segments"][1:] == other["segments"][1:]
    assert not options(client, twice)["undo"]["available"]

    elsewhere = edit(client, twice, 1, "Beta edited again")
    forward = ok(apply(client, elsewhere, "redo"))
    assert forward["segments"][0] == first["segments"][0]
    restored = ok(apply(client, forward, "redo"))
    assert restored["segments"][0] == last["segments"][0]
    assert restored["segments"][1:] == elsewhere["segments"][1:]
    assert restored["speakers"] == elsewhere["speakers"]

    undone = ok(apply(client, restored, "undo"))
    changed = edit(client, undone, 0, "A new branch of text")
    assert not options(client, changed)["redo"]["available"]


def test_passage_step_rejects_stale_head_and_other_passage_version(client, row):
    first = edit(client, row, 0, "Alpha edited")
    stale = options(client, first)
    second = edit(client, first, 1, "Beta edited")
    assert apply(client, second, "undo", stale).status_code == 409
    wrong = options(client, second, index=1)
    assert apply(client, second, "undo", wrong).status_code == 409
    assert content(client.app.state.store.recording(row["id"])) == content(second)
    assert ok(apply(client, second, "undo"))["segments"][1:] == second["segments"][1:]


def test_passage_history_stops_at_a_split_and_recording_undo_reverses_a_row_step(client, row):
    edited = edit(client, row, 0, "Alpha first edit")
    other = edit(client, edited, 2, "Gamma edited")
    reverted = ok(apply(client, other, "undo"))
    restored = ok(step(client, reverted))
    assert content(restored) == content(other)

    split = structure(client, restored, row["segments"][0]["id"], "split-v2", cursor=5)
    assert not options(client, split)["undo"]["available"]
    newer = edit(client, split, 0, "Alpha after the split")
    scoped = ok(apply(client, newer, "undo"))
    assert content(scoped) == content(split)
    assert not options(client, scoped)["undo"]["available"]


def test_passage_steps_restore_and_redo_exact_word_timings(client):
    from test_word_times_save import save, seed, typed

    _, row = seed(client)
    text = row["segments"][0]["text"]
    timed = ok(save(client, row, text, [typed(text, "København", 1.1, 1.6)]))
    edited = edit(client, timed, 1, "The other passage was changed.")
    assert options(client, edited)["undo"]["action"] == "word_timing_edit"
    previous = ok(apply(client, edited, "undo"))
    assert previous["segments"][0] == row["segments"][0]
    assert previous["segments"][1] == edited["segments"][1]
    redone = ok(apply(client, previous, "redo"))
    assert content(redone) == content(edited)


def test_unproven_single_passage_metadata_cannot_overwrite_multiple_passages(client, row):
    segments, speakers = content(row)
    segments[0]["text"] = "First altered passage"
    segments[1]["text"] = "Second altered passage"
    current = client.app.state.store.save_transcript(
        row["id"],
        segments,
        speakers,
        row["revision"],
        action="free_rewrite",
        context={"segment_id": segments[0]["id"]},
    )
    offered = options(client, current)
    assert not offered["undo"]["available"]
    version_id = current["active_version"]["id"]
    refused = client.post(
        f"{path(current)}/undo",
        json={
            "revision": current["revision"],
            "head_version_id": version_id,
            "version_id": version_id,
        },
    )
    assert refused.status_code == 409
    assert content(client.app.state.store.recording(row["id"])) == content(current)
