"""The packaged language choices are identifiers, not an ASR support list."""

import json
import re
from pathlib import Path


def test_packaged_language_choices_are_unique_searchable_and_pinned():
    path = Path(__file__).resolve().parents[1] / "src/cbs_transcriber/catalogs/languages.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    assert catalog["file_date"] == "2026-09-17"
    assert catalog["source_sha256"] == (
        "755fad43283be7b41ebe3c89ad054b6eaf928f404f9c0edb74799e0eab74beb1"
    )
    rows = catalog["languages"]
    assert [row["code"] for row in rows[:2]] == ["da", "en"]
    lookup = {row["code"]: row for row in rows}
    assert len(lookup) == len(rows) > 8000
    assert all(re.fullmatch(r"[a-z]{2,8}", code) for code in lookup)
    assert lookup["da"]["name"] == "Danish"
    assert lookup["en"]["name"] == "English"
    assert lookup["haw"]["name"] == "Hawaiian"
    assert "Cantonese" in lookup["yue"]["aliases"]
    assert "iw" in lookup["he"]["aliases"] and "iw" not in lookup
    assert not {"und", "mul", "mis", "zxx", "qaa"} & lookup.keys()
