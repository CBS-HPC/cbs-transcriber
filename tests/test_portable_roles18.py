"""Synthetic schema-18 Working history checks."""

import sqlite3

import pytest
from test_portable_semantics import RECORDING, VERSION, build, inspect


@pytest.mark.parametrize(
    "stamp", ["now", "2026-10-29", "2026-10-29T12:00:00", "2026-10-29\x0012:00:00+00:00", "x" * 65]
)
def test_invalid_role_observation_time_is_rejected(tmp_path, stamp):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE result_role_events SET observed_at=?", (stamp,))
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize("action", ["working_auto_successor", "working_auto_edit"])
def test_automatic_successor_records_system_actor_and_previous_role(tmp_path, action):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    successor = "5" * 32
    with sqlite3.connect(path) as db:
        row = list(db.execute("SELECT * FROM result_versions WHERE id=?", (VERSION,)).fetchone())
        row[0], row[2], row[3] = successor, 2, VERSION
        db.execute("INSERT INTO result_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
        db.execute(
            "UPDATE recording_result_roles SET working_version_id=?,revision=2 "
            "WHERE recording_id=?",
            (successor, RECORDING),
        )
        db.execute(
            "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "6" * 32,
                RECORDING,
                2,
                VERSION,
                row[6],
                successor,
                row[6],
                action,
                "2026-11-03T12:00:00+00:00",
                "system",
            ),
        )
    assert inspect(path)["semantic_counts"]["role_events"] == 2


def test_automatic_first_cannot_follow_an_earlier_clear(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        db.execute(
            "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "6" * 32,
                RECORDING,
                2,
                VERSION,
                payload_hash,
                None,
                None,
                "working_cleared",
                "2026-11-03T12:00:00+00:00",
                "local_user",
            ),
        )
        db.execute(
            "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "7" * 32,
                RECORDING,
                3,
                None,
                None,
                VERSION,
                payload_hash,
                "working_auto_first",
                "2026-11-03T12:00:00+00:00",
                "system",
            ),
        )
        db.execute(
            "UPDATE recording_result_roles SET revision=3 WHERE recording_id=?", (RECORDING,)
        )
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)


@pytest.mark.parametrize("empty", [False, True])
def test_explicit_keep_preserves_the_exact_frozen_role(tmp_path, empty):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        if empty:
            db.execute(
                "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    "6" * 32,
                    RECORDING,
                    2,
                    VERSION,
                    payload_hash,
                    None,
                    None,
                    "working_cleared",
                    "2026-11-03T12:00:00+00:00",
                    "local_user",
                ),
            )
        version, digest = (None, None) if empty else (VERSION, payload_hash)
        sequence = 3 if empty else 2
        db.execute(
            "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "7" * 32,
                RECORDING,
                sequence,
                version,
                digest,
                version,
                digest,
                "working_kept",
                "2026-11-03T12:00:00+00:00",
                "local_user",
            ),
        )
        db.execute(
            "UPDATE recording_result_roles SET working_version_id=?,revision=? "
            "WHERE recording_id=?",
            (version, sequence, RECORDING),
        )
    assert inspect(path)["semantic_counts"]["role_events"] == sequence


@pytest.mark.parametrize("damage", ["changed", "actor"])
def test_explicit_keep_rejects_changed_role_or_system_actor(tmp_path, damage):
    path = tmp_path / "synthetic.sqlite3"
    build(path, 18)
    with sqlite3.connect(path) as db:
        payload_hash = db.execute(
            "SELECT payload_sha256 FROM result_versions WHERE id=?", (VERSION,)
        ).fetchone()[0]
        target, digest = (None, None) if damage == "changed" else (VERSION, payload_hash)
        db.execute(
            "INSERT INTO result_role_events VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "7" * 32,
                RECORDING,
                2,
                VERSION,
                payload_hash,
                target,
                digest,
                "working_kept",
                "2026-11-03T12:00:00+00:00",
                "system" if damage == "actor" else "local_user",
            ),
        )
        db.execute(
            "UPDATE recording_result_roles SET working_version_id=?,revision=2 "
            "WHERE recording_id=?",
            (target, RECORDING),
        )
    with pytest.raises(ValueError, match="inspection"):
        inspect(path)
