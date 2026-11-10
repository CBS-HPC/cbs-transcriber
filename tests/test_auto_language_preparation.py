"""Automatic full-ASR preparation and partial checkpoint carry stay offline and frozen."""

import copy
import json
from types import SimpleNamespace

import pytest
from test_auto_language_pipeline import checkpoint

from cbs_transcriber import enhancement_api as api
from cbs_transcriber import enhancement_worker as worker
from cbs_transcriber import providers
from cbs_transcriber.enhancement import EnhancementOptions


@pytest.fixture
def auto_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda name: "1.2.1")
    options = EnhancementOptions(language="auto", transcribe=True, whisper_model="large-v3-turbo")
    checkpoint(tmp_path, options)
    frozen = json.loads((tmp_path / "request.json").read_bytes())["options"]
    return tmp_path, frozen


@pytest.mark.parametrize("language", ["auto", "da", "en"])
@pytest.mark.parametrize("model", ["small", "large-v3-turbo"])
@pytest.mark.parametrize("align", [False, True])
@pytest.mark.parametrize("diarize", [False, True])
def test_required_models_preserves_fixed_order_and_prepares_both_auto_aligners(
    language, model, align, diarize
):
    options = SimpleNamespace(
        language=language,
        whisper_model=model,
        align=align,
        diarize=diarize,
        transcribe=True,
        retranscribe=False,
    )
    frozen = json.dumps(vars(options), sort_keys=True)
    selected = "coarse" if model == "small" else "whisper-large-v3-turbo"
    expected = [selected]
    if align:
        expected += (
            (["da", "en"] if language == "auto" else [language])
            if selected == "coarse"
            else (["coarse", "da", "en"] if language == "auto" else ["coarse", language])
        )
    if diarize:
        expected += ["speakers"]
    assert api.required_models(options) == expected
    assert api.prefetch_models(options) == [name for name in expected if name != "speakers"]
    assert "auto" not in api.required_models(options)
    assert json.dumps(vars(options), sort_keys=True) == frozen


def settings():
    return providers.Settings(
        provider_type="ucloud",
        base_url="https://cloud.invalid",
        model="large-v3-turbo",
        ucloud_transcriber={"storage_path": "/drive/cbs"},
    )


@pytest.mark.parametrize("missing", [None, "whisper-large-v3-turbo", "coarse", "da", "en"])
def test_auto_readiness_requires_all_pinned_public_models_without_speaker_access(
    monkeypatch, missing
):
    configured = settings()
    options = EnhancementOptions(
        language="auto", transcribe=True, execution="ucloud", whisper_model="large-v3-turbo"
    )
    seen = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def listed(client, path):
        seen.append(path)
        return missing is None or path != api.model_folder(configured, missing)

    monkeypatch.setattr(api.native, "client_for", lambda *a: Client())
    monkeypatch.setattr(api, "stamped", lambda *a: True)
    monkeypatch.setattr(api, "listed", listed)
    monkeypatch.setattr(
        api, "speaker_model_available", lambda *a: pytest.fail("unrequested gated model check")
    )
    credentials = SimpleNamespace(get=lambda *a: "synthetic")
    assert api.whisperx_ready(credentials, {"settings": configured.model_dump()}, options) is (
        missing is None
    )
    if missing is None:
        assert seen == [
            api.model_folder(configured, name)
            for name in ("whisper-large-v3-turbo", "coarse", "da", "en")
        ]
    else:
        assert api.model_folder(configured, missing) in seen


def test_auto_explicit_preparation_lists_public_models_only():
    options = EnhancementOptions(
        language="auto",
        transcribe=True,
        diarize=True,
        execution="ucloud",
        whisper_model="large-v3-turbo",
    )
    script = api.supervisor_script(settings(), "synthetic", True, api.prefetch_models(options))
    assert 'MODELS="whisper-large-v3-turbo,coarse,da,en"' in script
    assert "speakers" not in script.split("MODELS=")[1][:60]
    assert api.required_models(options)[-1] == "speakers"


@pytest.mark.parametrize("bad", ["malformed", "oversized", "foreign_outcome"])
def test_asr_only_carry_ignores_bad_alignment_without_relabeling_it_reusable(
    auto_stage, monkeypatch, bad
):
    folder, options = auto_stage
    path = folder / "alignment.json"
    if bad == "malformed":
        path.write_text("not JSON", encoding="utf-8")
    elif bad == "oversized":
        monkeypatch.setattr(worker, "AUTO_JSON_LIMIT", 2048)
        path.write_bytes(b" " * 2049)
    else:
        value = json.loads(path.read_bytes())
        value["language_outcome"]["decision_sha256"] = "0" * 64
        worker.write_json(path, value)
    before_request = (folder / "request.json").read_bytes()
    with pytest.raises(ValueError):
        worker.auto_transcription_metadata(folder, options)
    real_open = type(path).open

    def guard(candidate, *args, **kwargs):
        if candidate == path:
            pytest.fail("ASR-only carry read the failed alignment artifact")
        return real_open(candidate, *args, **kwargs)

    monkeypatch.setattr(type(path), "open", guard)
    metadata = worker.auto_transcription_metadata(folder, options, include_alignment=False)
    assert set(metadata) == {"language_decision"}
    assert metadata["language_decision"]["status"] == "resolved"
    assert (folder / "request.json").read_bytes() == before_request


@pytest.mark.parametrize("flag", [None, 0, 1, "false", []])
def test_asr_only_flag_requires_literal_bool(auto_stage, flag):
    folder, options = auto_stage
    with pytest.raises(ValueError):
        worker.auto_transcription_metadata(folder, options, include_alignment=flag)


def test_asr_only_carry_cannot_bypass_required_alignment_or_invalid_asr(auto_stage):
    folder, options = auto_stage
    with pytest.raises(ValueError, match="cannot bypass"):
        worker.auto_transcription_metadata(
            folder, options, include_alignment=False, require_alignment=True
        )
    path = folder / "transcription.json"
    value = json.loads(path.read_bytes())
    value["language_decision"]["request_sha256"] = "0" * 64
    worker.write_json(path, value)
    with pytest.raises(ValueError, match="another prepared input or request"):
        worker.auto_transcription_metadata(folder, options, include_alignment=False)


@pytest.mark.parametrize(
    "bad",
    ["negative", "reversed", "recording_end", "passage_start", "passage_end", "unordered", "huge"],
)
def test_aligned_numeric_violations_refuse_completion_but_preserve_valid_asr(auto_stage, bad):
    folder, options = auto_stage
    path = folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    words = alignment["passages"][0]["words"]
    if bad == "negative":
        words[0].update(start=-0.01, end=0.5)
    elif bad == "reversed":
        words[0].update(start=0.5, end=0.4)
    elif bad == "recording_end":
        words[-1].update(start=1.0, end=3.11)
    elif bad == "passage_start":
        words[0].update(start=0.09, end=0.5)
    elif bad == "passage_end":
        words[-1].update(start=1.0, end=2.11)
    elif bad == "unordered":
        words[1].update(start=0.29, end=0.5)
    elif bad == "huge":
        words[0]["start"] = 10**400
    worker.write_json(path, alignment)
    before_asr = (folder / "transcription.json").read_bytes()
    with pytest.raises(ValueError):
        worker.auto_transcription_metadata(folder, options, require_alignment=True)
    assert set(worker.auto_transcription_metadata(folder, options, include_alignment=False)) == {
        "language_decision"
    }
    assert (folder / "transcription.json").read_bytes() == before_asr


@pytest.mark.parametrize(
    "partial",
    [
        {"start": None, "end": None, "score": None},
        {"start": 999.0, "end": 1000.0, "score": None},
        {"start": True, "end": 0.5, "score": 0.9},
        {"start": float("nan"), "end": 0.5, "score": 0.9},
        {"start": 0.3, "end": 0.3, "score": 0.9},
        {"start": 0.1, "end": 0.5, "score": -1.0},
    ],
)
def test_aligned_partial_unknown_or_zero_length_words_remain_host_cleanable(auto_stage, partial):
    folder, options = auto_stage
    path = folder / "alignment.json"
    alignment = json.loads(path.read_bytes())
    alignment["passages"][0]["words"][0].update(copy.deepcopy(partial))
    path.write_text(json.dumps(alignment), encoding="utf-8")
    metadata = worker.auto_transcription_metadata(folder, options, require_alignment=True)
    assert metadata["alignment_outcome"]["state"] == "aligned"
