"""Synthetic design proofs only; the example is outside the application package."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from docs.examples import anonymisation_evaluation

EXAMPLE = Path(__file__).resolve().parents[1] / "docs/examples/anonymisation_demo.py"
SPEC = importlib.util.spec_from_file_location("anonymisation_design_example", EXAMPLE)
demo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo)


def source(text=None):
    snapshot = demo.synthetic_source()
    if text is not None:
        snapshot["payload"]["segments"][0]["text"] = text
        payload = json.dumps(
            snapshot["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        snapshot["binding"]["payload_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return snapshot


def derive(snapshot, hits, decisions, *, expected=None, speakers=None, entity_labels=None):
    return demo.derive(
        snapshot,
        expected or dict(snapshot["binding"]),
        hits,
        decisions,
        {"HIDDEN_VOICE_ID": "[VOICE_A]"} if speakers is None else speakers,
        entity_labels=entity_labels,
    )


def test_demo_exports_only_text_reviewed_voice_aliases_and_fixed_method():
    result = demo.demo()
    public = result["shareable"]
    serialized = json.dumps(public, ensure_ascii=False)
    assert set(public) == {"format", "method", "segments", "speakers"}
    assert set(public["segments"][0]) == {"passage", "text", "speaker"}
    assert public["segments"][0]["text"] == "🙂 [PERSON] skrev til [EMAIL].\r\nBevar e\u0301 og Ø."
    assert public["speakers"] == {"voice-1": "[VOICE_A]"}
    assert "HIDDEN_" not in serialized
    assert "SYNTHETIC_PERSON_ALPHA" not in serialized and "demo@example.invalid" not in serialized
    assert result["private_receipt"]["spans"][0]["quote"] == "SYNTHETIC_PERSON_ALPHA"
    assert result["private_receipt"]["speaker_labels"] == {"HIDDEN_VOICE_ID": "[VOICE_A]"}
    digest = hashlib.sha256(
        json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    assert result["private_receipt"]["output_sha256"] == digest


def test_distinct_speaker_reviews_have_distinct_private_output_bound_evidence():
    snapshot = source("Non-identifying synthetic content.")
    first = derive(snapshot, [], [], speakers={"HIDDEN_VOICE_ID": "[VOICE_A]"})
    second = derive(snapshot, [], [], speakers={"HIDDEN_VOICE_ID": "[VOICE_B]"})
    assert first["private_receipt"] != second["private_receipt"]
    assert first["private_receipt"]["output_sha256"] != second["private_receipt"]["output_sha256"]
    assert first["private_receipt"]["ruleset"] == demo.RULESET
    assert "HIDDEN_" not in json.dumps(first["shareable"])
    assert "HIDDEN_" not in json.dumps(second["shareable"])


def test_cli_only_prints_shareable_json_under_ascii_console_encoding():
    output = subprocess.run(
        [sys.executable, str(EXAMPLE)], capture_output=True, check=True, timeout=5
    )
    assert output.stderr == b""
    assert json.loads(output.stdout.decode("ascii")) == demo.demo()["shareable"]
    assert b"private_receipt" not in output.stdout and b"HIDDEN_" not in output.stdout


def test_unicode_offsets_repeated_mentions_and_keep_are_literal_and_independent():
    snapshot = source("\n🙂 e\u0301 SYNTHETIC_A, SYNTHETIC_A.\r\n")
    before = copy.deepcopy(snapshot)
    hits = demo.suggest(snapshot, ("e\u0301", "SYNTHETIC_A"))
    assert [hit["quote"] for hit in hits] == ["e\u0301", "SYNTHETIC_A", "SYNTHETIC_A"]
    assert hits[0]["start"] == 3  # UTF-16 would count the preceding emoji as two units: 4.
    result = derive(
        snapshot,
        hits,
        [{"action": "keep"}, {"action": "replace", "text": "[ONE]"}, {"action": "keep"}],
    )
    assert result["shareable"]["segments"][0]["text"] == "\n🙂 e\u0301 [ONE], SYNTHETIC_A.\r\n"
    assert snapshot == before


def test_known_literals_do_not_match_word_or_combining_mark_fragments():
    snapshot = source("SYNTHETIC_AA SYNTHETIC_A e\u0301 e Øx Ø")
    hits = demo.suggest(snapshot, ("SYNTHETIC_A", "e", "Ø"))
    assert [hit["quote"] for hit in hits] == ["SYNTHETIC_A", "e", "Ø"]


@pytest.mark.parametrize("field", ["version_id", "result_id", "recording_id", "role_revision"])
def test_stale_source_or_working_identity_cannot_rebase_decisions(field):
    snapshot = source()
    expected = dict(snapshot["binding"])
    hits = demo.suggest(snapshot)
    snapshot["binding"][field] = 4 if field == "role_revision" else "different-synthetic-source"
    with pytest.raises(ValueError, match="changed"):
        derive(snapshot, hits, [{"action": "keep"}], expected=expected)


def test_changed_payload_cannot_claim_old_source_hash():
    snapshot = source()
    expected = dict(snapshot["binding"])
    snapshot["payload"]["segments"][0]["text"] += "changed"
    with pytest.raises(ValueError, match="changed"):
        derive(snapshot, [], [], expected=expected)


@pytest.mark.parametrize("change", ["quote", "source", "unknown-field"])
def test_literal_span_cannot_assert_a_different_quote_or_source(change):
    snapshot = source()
    hits = demo.suggest(snapshot)
    if change == "source":
        hits[0]["source"]["version_id"] = "wrong-synthetic-version"
    else:
        hits[0][change] = "invented-synthetic-quote"
    with pytest.raises(ValueError, match="exact source"):
        derive(snapshot, hits, [{"action": "keep"}])


def test_manual_span_overlap_requires_explicit_resolution_and_complete_review():
    snapshot = source("SYNTHETIC_A@example.invalid")
    email = demo.suggest(snapshot)[0]
    manual = demo.span(snapshot, 0, 0, len("SYNTHETIC_A"))
    decisions = [
        {"action": "replace", "text": "[EMAIL]"},
        {"action": "replace", "text": "[PERSON]"},
    ]
    with pytest.raises(ValueError, match="overlapping"):
        derive(snapshot, [email, manual], decisions)
    with pytest.raises(ValueError, match="Every suggestion"):
        derive(snapshot, [email, manual], decisions[:1])
    result = derive(snapshot, [email, manual], [decisions[0], {"action": "keep"}])
    assert result["shareable"]["segments"][0]["text"] == "[EMAIL]"


@pytest.mark.parametrize("start,end", [(True, 3), (-1, 2), (1, 1), (0, 999)])
def test_manual_selection_requires_actual_codepoint_bounds(start, end):
    with pytest.raises(ValueError, match="coordinates"):
        demo.span(source("🙂 e\u0301"), 0, start, end)


def test_every_speaker_requires_review_even_when_all_text_suggestions_are_rejected():
    snapshot = source()
    hits = demo.suggest(snapshot)
    with pytest.raises(ValueError, match="speaker"):
        derive(snapshot, hits, [{"action": "keep"}], speakers={})
    result = derive(snapshot, hits, [{"action": "keep"}])
    assert "demo@example.invalid" in result["shareable"]["segments"][0]["text"]
    assert "HIDDEN_" not in json.dumps(result["shareable"])


def test_passage_offsets_never_cross_or_join_source_passages():
    snapshot = source("SYNTHETIC_")
    snapshot["payload"]["segments"].append({"text": "A", "speaker": "HIDDEN_VOICE_ID"})
    encoded = json.dumps(
        snapshot["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    snapshot["binding"]["payload_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    assert demo.suggest(snapshot, ("SYNTHETIC_A",)) == []
    result = derive(snapshot, [], [])
    assert [item["text"] for item in result["shareable"]["segments"]] == ["SYNTHETIC_", "A"]


def test_explicit_entity_links_keep_variants_consistent_without_linking_same_spelling():
    labels = {"HIDDEN_ENTITY_1": "[PERSON_1]", "HIDDEN_ENTITY_2": "[PERSON_2]"}
    first = source("SYNTHETIC_A, SYNTHETIC_A.")
    hits = demo.suggest(first, ("SYNTHETIC_A",))
    decisions = [
        {"action": "replace", "entity": "HIDDEN_ENTITY_1"},
        {"action": "replace", "entity": "HIDDEN_ENTITY_2"},
    ]
    result = derive(first, hits, decisions, entity_labels=labels)
    assert result["shareable"]["segments"][0]["text"] == "[PERSON_1], [PERSON_2]."
    second = source("SYNTHETIC_AS")
    variant = derive(
        second,
        demo.suggest(second, ("SYNTHETIC_AS",)),
        [{"action": "replace", "entity": "HIDDEN_ENTITY_1"}],
        entity_labels=labels,
    )
    assert variant["shareable"]["segments"][0]["text"] == "[PERSON_1]"
    assert "HIDDEN_" not in json.dumps(result["shareable"])
    assert "HIDDEN_" not in json.dumps(variant["shareable"])
    assert result["private_receipt"]["entity_labels"] == labels
    labels["HIDDEN_ENTITY_1"] = "changed later"
    decisions[0]["entity"] = "changed later"
    assert result["private_receipt"]["entity_labels"]["HIDDEN_ENTITY_1"] == "[PERSON_1]"
    assert result["private_receipt"]["decisions"][0]["entity"] == "HIDDEN_ENTITY_1"


@pytest.mark.parametrize(
    "labels",
    [None, {"entity": ""}, {"entity": "[P]", "other": "[P]"}, {"": "[P]"}],
)
def test_entity_decisions_refuse_missing_empty_or_colliding_labels(labels):
    snapshot = source("SYNTHETIC_A")
    with pytest.raises(ValueError, match="entity|entities"):
        derive(
            snapshot,
            demo.suggest(snapshot, ("SYNTHETIC_A",)),
            [{"action": "replace", "entity": "entity"}],
            entity_labels=labels,
        )


def test_review_does_not_certify_replacement_wording_as_nonidentifying():
    snapshot = source("SYNTHETIC_A")
    result = derive(
        snapshot,
        demo.suggest(snapshot, ("SYNTHETIC_A",)),
        [{"action": "replace", "entity": "private-entity"}],
        entity_labels={"private-entity": "SYNTHETIC_REINTRODUCED_IDENTIFIER"},
    )
    assert result["shareable"]["segments"][0]["text"] == "SYNTHETIC_REINTRODUCED_IDENTIFIER"


def test_bilingual_fixture_report_exposes_exact_misses_and_false_suggestions_only():
    report = anonymisation_evaluation.evaluate()
    assert report["languages"]["da"] == {
        "cases": 3,
        "gold_mentions": 7,
        "exact_hits": 3,
        "false_suggestions": 0,
        "missed_mentions": 4,
        "cases_with_misses": 2,
        "clusters": 4,
        "clusters_with_misses": 3,
        "categories": {
            "person": {"gold": 3, "exact_hits": 2},
            "email": {"gold": 1, "exact_hits": 1},
            "place": {"gold": 2, "exact_hits": 0},
            "context": {"gold": 1, "exact_hits": 0},
        },
    }
    assert report["languages"]["en"] == {
        "cases": 3,
        "gold_mentions": 5,
        "exact_hits": 3,
        "false_suggestions": 1,
        "missed_mentions": 2,
        "cases_with_misses": 1,
        "clusters": 2,
        "clusters_with_misses": 1,
        "categories": {
            "person": {"gold": 3, "exact_hits": 1},
            "email": {"gold": 2, "exact_hits": 2},
        },
    }
    serialized = json.dumps(report)
    assert "not model or research-data accuracy" in report["scope"]
    for canary in ("HIDDEN_", "Fiktivia", "Fictional", "@", "da-person", "en-email"):
        assert canary not in serialized
