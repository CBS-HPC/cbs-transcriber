"""The preparation pack is exact synthetic evidence, never desktop acceptance."""

import csv
import hashlib
import importlib.util
import io
import json
import socket
import wave
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "nvivo_validation_pack", ROOT / "scripts/build_nvivo_validation_pack.py"
)
PACK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACK)


@pytest.fixture
def pack(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Pack generation attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    original = PACK.FIXTURE.read_bytes()
    output = tmp_path / "pack"
    manifest = PACK.build(output)
    assert PACK.FIXTURE.read_bytes() == original
    return output, manifest


def test_pack_binds_literal_sources_coding_states_and_exact_working_exports(pack):
    output, manifest = pack
    assert manifest["desktop_acceptance"] == "NOT_RUN"
    assert manifest["refi_qda_app_support"] == "NOT_IMPLEMENTED"
    assert not list(output.rglob("*.sqlite*"))
    assert not list(output.rglob("*.qdc")) and not list(output.rglob("*.qdpx"))
    for entry in manifest["files"]:
        data = (output / entry["path"]).read_bytes()
        assert len(data) == entry["bytes"]
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    identities = json.loads((output / "identities.json").read_bytes())
    fixture = json.loads((output / "fixture.json").read_bytes())
    for case in fixture["cases"]:
        directory = output / case["id"]
        source = (directory / "source-version.json").read_bytes()
        identity = next(item for item in identities if item["case"] == case["id"])
        assert hashlib.sha256(source).hexdigest() == identity["payload_sha256"]
        decoded = json.loads((directory / "transcript.json").read_bytes())
        assert [s["text"] for s in decoded["segments"]] == [s["text"] for s in case["segments"]]
        assert "\r\n" in decoded["segments"][2]["text"]
        assert "café" in decoded["segments"][2]["text"]
        assert "cafe\u0301" in decoded["segments"][2]["text"]
        coding = json.loads((directory / "reviewed-coding.json").read_bytes())
        assert coding["run"]["source_version_id"] == identity["version_id"]
        assert coding["run"]["source_payload_sha256"] == identity["payload_sha256"]
        assert [item["review_state"] for item in coding["items"]] == [
            "accepted",
            "accepted",
            "rejected",
            "unreviewed",
            "accepted",
        ]
        assert "annotation" not in coding["items"][3]
        assert "producer" not in coding["run"]
        assert PACK.PRIVATE_CANARY not in json.dumps(coding)
        positive = coding["items"][0]["annotation"]["evidence"][0]
        assert positive["start_char"] == case["coding"]["positive"]["start_char"]
        prefix = case["segments"][1]["text"][: positive["start_char"]]
        assert len(prefix.encode("utf-16-le")) // 2 == positive["start_char"] + 1
        assert (
            PACK.annotations.canonical(coding["codebook"])[1]
            == coding["run"]["codebook_payload_sha256"]
        )
        for item in coding["items"]:
            for span in item.get("annotation", {}).get("evidence", []):
                passage = json.loads(source)["segments"][int(span["passage_id"])]
                assert passage["text"][span["start_char"] : span["end_char"]] == span["exact_text"]
        assert coding["items"][-1]["annotation"]["evidence"][0]["exact_text"] == "cafe\u0301"
        with wave.open(str(directory / "clock-no-speech.wav")) as clock:
            assert (clock.getnchannels(), clock.getsampwidth(), clock.getframerate()) == (
                1,
                2,
                16000,
            )
            assert clock.getnframes() == 192000
            clock.setpos(6 * 16000)
            assert clock.readframes(160) == bytes(320)
            clock.setpos(97601)  # 6.1-second cue + one frame into the 1 kHz tone.
            assert clock.readframes(1) != bytes(2)
    for format_name, filename in (
        ("nvivo-windows", "windows.txt"),
        ("nvivo-mac", "mac.txt"),
        ("json", "transcript.json"),
    ):
        with ZipFile(output / f"working-project-{format_name}.zip") as archive:
            report = json.loads(archive.read("manifest.json"))
            assert len(report["entries"]) == 5
            for entry in report["entries"]:
                if entry["status"] == "exported":
                    identity = next(
                        i for i in identities if i["recording_id"] == entry["recording_id"]
                    )
                    assert (
                        archive.read(entry["path"])
                        == (output / identity["case"] / filename).read_bytes()
                    )


def test_pack_refusals_preserve_untimed_text_and_never_claim_manual_acceptance(pack):
    output, _ = pack
    refusals = json.loads((output / "refusals.json").read_bytes())
    assert {(item["case"], item["format"]) for item in refusals} == {
        ("missing-start", "nvivo-windows"),
        ("missing-start", "nvivo-mac"),
        ("missing-start", "srt"),
        ("missing-start", "vtt"),
        ("start-only", "srt"),
        ("start-only", "vtt"),
    }
    for item in refusals:
        assert not (output / item["case"] / PACK.FORMATS[item["format"]]).exists()
    missing = json.loads((output / "missing-start/transcript.json").read_bytes())
    assert missing["segments"][1]["start"] is None
    assert missing["segments"][1]["text"] == "No timing. Ingen tid. 🙂"
    start_only = (output / "start-only/windows.txt").read_text(encoding="utf-8-sig")
    assert "01:00:00.0\tSpeaker_01\t" in start_only
    results = list(
        csv.DictReader(io.StringIO((output / "manual-results.csv").read_text(encoding="utf-8-sig")))
    )
    assert {row["case_id"] for row in results} == set(PACK.CASES)
    assert all(row["status"] == "NOT_RUN" and not row["observed"] for row in results)
    original = (output / "pack-manifest.json").read_bytes()
    with pytest.raises(FileExistsError):
        PACK.build(output)
    assert (output / "pack-manifest.json").read_bytes() == original


def test_pack_refuses_private_catalog_before_creating_output(tmp_path, monkeypatch):
    monkeypatch.setenv("CBS_TRANSCRIBER_MODELS", "not-a-file-do-not-read")
    output = tmp_path / "never-created"
    with pytest.raises(RuntimeError, match="CBS_TRANSCRIBER_MODELS"):
        PACK.build(output)
    assert not output.exists()
