"""Bounded offline alignment, without installing any inference libraries or model weights."""

import copy
import json
import re
import struct
import sys
import wave
from types import SimpleNamespace

import pytest

from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber.enhancement import EnhancementOptions, merge_output, validate_input
from cbs_transcriber.transcripts import edit_segment, normalize_segments


def recognized(words, offset=0, spacing=0.4):
    return [
        {"word": word, "start": offset + i * spacing, "end": offset + i * spacing + 0.3}
        for i, word in enumerate(words)
    ]


def test_v33_long_windows_preserve_wording_and_bound_inference(tmp_path):
    text = "  ".join(f"ord{i:04d}" for i in range(340)) + " 🙂"
    passages = [{"id": "0", "text": text, "start": None, "end": None}]
    before = copy.deepcopy(passages)
    words = re.findall(r"\S+", text)
    windows = worker.alignment_windows(passages, recognized(words[:-1], 50), 400)
    assert len(windows) >= 3
    calls = []

    def align(rows, *args, **kwargs):
        row = rows[0]
        calls.append(row)
        assert kwargs["interpolate_method"] == "ignore"
        assert 0 < row["end"] - row["start"] <= 60
        assert len(row["text"].split()) <= 120 and len(row["text"]) <= 1200
        # Synthetic actual engine output, deliberately different from preliminary ASR times.
        return {
            "word_segments": [
                {
                    "word": w,
                    "start": row["start"] + i * 0.2,
                    "end": row["start"] + i * 0.2 + 0.1,
                    "score": 0.9,
                }
                for i, w in enumerate(row["text"].split())
            ]
        }

    options = EnhancementOptions().model_dump() | {"duration": 400}
    result = worker.align_passages(
        SimpleNamespace(align=align),
        None,
        {},
        [],
        passages,
        options,
        lambda *a: None,
        windows,
        tmp_path,
    )
    assert len(calls) == len(windows) and passages == before
    assert result["passages"][0]["words"][0]["start"] != 50
    source = {"segments": normalize_segments(passages), "speakers": {}}
    rows, _ = merge_output(source, result, None, EnhancementOptions(), 400, "scope")
    assert "".join(s["text"] for s in rows) == text
    assert rows[0]["word_timing"]
    assert not source["segments"][0]["word_timing"]
    # All successful windows are durable and can be reused without another alignment call.
    calls.clear()
    worker.align_passages(
        SimpleNamespace(align=align),
        None,
        {},
        [],
        passages,
        options,
        lambda *a: None,
        windows,
        tmp_path,
    )
    assert calls == []


def test_v33_unanchored_omissions_stay_untimed_and_keep_every_character():
    first, last = "Hej København vi mødes".split(), "nu fortsætter anden samtale her".split()
    missing = [f"ukendt{i}" for i in range(24)]
    text = "🙂 " + "  ".join(first + missing + last) + "!"
    passages = [{"id": "0", "text": text, "start": None, "end": None}]
    coarse = recognized(first, 10) + recognized(last, 150)
    windows = worker.alignment_windows(passages, coarse, 300)
    assert len(windows) == 2
    flattened = text.split()
    covered = {i for w in windows for i in range(w["first"], w["last"] + 1)}
    assert not any(flattened[i].startswith("ukendt") for i in covered)
    raw = {
        "passages": [
            {
                "id": "0",
                "words": [
                    {
                        "word": word,
                        "start": 10 + i if i in covered else None,
                        "end": 10.5 + i if i in covered else None,
                        "score": 0.9 if i in covered else None,
                    }
                    for i, word in enumerate(flattened)
                ],
            }
        ]
    }
    source = {"segments": normalize_segments(passages), "speakers": {}}
    rows, _ = merge_output(source, raw, None, EnhancementOptions(), 300, "run")
    assert len(rows) == 3 and "".join(s["text"] for s in rows) == text
    assert [s["word_timing"] for s in rows] == [True, False, True]
    assert rows[1]["start"] is None and rows[1]["end"] is None
    assert "".join(edit_segment(s, "restore")["text"] for s in rows) == text


def test_v33_ambiguous_and_conflicting_phrases_are_not_audio_anchors():
    repeated = "ja det ved jeg".split() * 30
    assert worker.distinctive_pairs(repeated, repeated) == []
    left = "one two three four separator five six seven eight".split()
    right = "five six seven eight separator one two three four".split()
    assert worker.distinctive_pairs(left, right) == []
    assert worker.coarse_windows(repeated, recognized(repeated), 300) == []
    # A clear phrase still matches after much repeated filler and a provider substitution.
    source = repeated + "vi undersøger Københavns grønne byrum".split()
    target = repeated + "vi undersøger Københavns grønne områder".split()
    pairs = worker.distinctive_pairs(source, target)
    # Up to three preceding fillers gain disambiguating context from the unique phrase.
    assert pairs and all(a >= len(repeated) - 3 for a, _ in pairs)


def test_v33_known_passage_bounds_and_dense_text_are_respected():
    words = [f"word{i}" for i in range(160)]
    passages = [
        {"id": "a", "text": " ".join(words), "start": 20, "end": 120},
        {"id": "b", "text": "Short timed passage.", "start": 140, "end": 144},
    ]
    windows = worker.alignment_windows(passages, recognized(words, 10), 180)
    assert all(20 <= w["start"] < w["end"] <= 120 for w in windows[:-1])
    assert windows[0]["first"] >= 25  # Early recognized words contradict the imported window.
    assert windows[-1] == {"first": 160, "last": 162, "start": 140, "end": 144}
    assert worker.needs_coarse(passages, 180)
    dense = [{"id": "0", "text": " ".join(words), "start": 0, "end": 3}]
    assert worker.needs_coarse(dense, 3)
    source = {"segments": normalize_segments(dense), "speakers": {}}
    assert not validate_input(source, EnhancementOptions(), 3)


def test_v32_recognition_retry_uses_saved_slices_and_restores_offsets(tmp_path):
    calls, fail = [], [True]

    class Audio:
        def __getitem__(self, key):
            assert key.stop - key.start <= 30 * 16000
            return key.start / 16000

    def transcribe(offset, **kwargs):
        assert kwargs["word_timestamps"] and not kwargs["condition_on_previous_text"]
        assert not kwargs["vad_filter"]
        calls.append(offset)
        if offset == 26 and fail[0]:
            raise RuntimeError("synthetic interrupted recognition")
        words = [SimpleNamespace(word="København", start=3, end=4, probability=0.95)]
        return iter([SimpleNamespace(words=words)]), None

    model = SimpleNamespace(transcribe=transcribe)
    options = {"duration": 65, "language": "da"}
    with pytest.raises(RuntimeError):
        worker.recognize_windows(model, Audio(), options, tmp_path, lambda *a: None)
    assert (tmp_path / "recognition-00000.json").exists()
    fail[0] = False
    rows = worker.recognize_windows(model, Audio(), options, tmp_path, lambda *a: None)
    assert calls == [0, 26, 26, 52]
    assert [w["start"] for w in rows] == [3, 29, 55]


def test_v33_pcm_conversion_is_disk_backed_and_reads_bounded_blocks(tmp_path, monkeypatch):
    with wave.open(str(tmp_path / "audio.wav"), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<hh", -32768, 16384) * (16000 * 31))
    original = wave.open
    reads = []

    class Reader:
        def __enter__(self):
            self.wav = original(str(tmp_path / "audio.wav"), "rb")
            return self

        def __exit__(self, *args):
            self.wav.close()

        def __getattr__(self, name):
            return getattr(self.wav, name)

        def readframes(self, count):
            reads.append(count)
            assert count <= 16000 * 30
            return self.wav.readframes(count)

    monkeypatch.setattr(worker.wave, "open", lambda *a: Reader())
    path = worker.prepare_audio(tmp_path)
    assert path.stat().st_size == 16000 * 62 * 4
    with path.open("rb") as stream:
        assert struct.unpack("<ff", stream.read(8)) == (-1, 0.5)
    assert len(reads) >= 3
    reads.clear()
    assert worker.prepare_audio(tmp_path) == path and reads == []
    calls = []
    monkeypatch.setitem(
        sys.modules,
        "numpy",
        SimpleNamespace(memmap=lambda path, **kwargs: calls.append((path, kwargs)) or "disk-array"),
    )
    assert worker.audio_data(tmp_path) == "disk-array"
    assert calls == [(path, {"dtype": "<f4", "mode": "r+"})]


def test_v32_long_pipeline_reuses_coarse_windows_after_alignment_failure(tmp_path, monkeypatch):
    calls, fail = [], [True]
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    text = "Hej København vi mødes"

    class Preliminary:
        def __init__(self, path, **kwargs):
            calls.append("model")
            assert kwargs["local_files_only"] and kwargs["compute_type"] == "int8"

        def transcribe(self, offset, **kwargs):
            calls.append("recognize")
            words = (
                [
                    SimpleNamespace(word=w, start=2 + i, end=2.5 + i, probability=0.99)
                    for i, w in enumerate(text.split())
                ]
                if offset == 0
                else []
            )
            return iter([SimpleNamespace(words=words)]), None

    class Audio:
        def __getitem__(self, key):
            return key.start / 16000

    def align(rows, *args, **kwargs):
        calls.append("align")
        if fail[0]:
            raise RuntimeError("synthetic stage failure")
        return {
            "word_segments": [
                dict(word=w, start=2 + i, end=2.5 + i, score=0.9)
                for i, w in enumerate(rows[0]["text"].split())
            ]
        }

    def snapshot(repo, **kwargs):
        assert kwargs["local_files_only"] and kwargs["token"] is False
        assert (repo, kwargs["revision"]) in worker.MODELS.values()
        return str(tmp_path)

    modules = {
        "torch": SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)),
        "whisperx": SimpleNamespace(align=align, load_align_model=lambda *a, **kw: (None, {})),
        "faster_whisper": SimpleNamespace(WhisperModel=Preliminary),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot),
        "nltk": SimpleNamespace(data=SimpleNamespace(load=lambda *a: None)),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(worker, "audio_data", lambda *a: Audio())
    request = {
        "passages": [{"id": "0", "text": text, "start": None, "end": None}],
        "options": EnhancementOptions().model_dump() | {"duration": 90},
    }
    with pytest.raises(RuntimeError):
        worker.execute(request, tmp_path)
    assert (tmp_path / "windows.json").exists()
    assert not (tmp_path / "alignment.json").exists()
    fail[0] = False
    assert worker.execute(request, tmp_path) is None
    assert calls.count("model") == 1 and calls.count("align") == 2
    assert len(json.loads((tmp_path / "alignment.json").read_text())["passages"][0]["words"]) == 4
