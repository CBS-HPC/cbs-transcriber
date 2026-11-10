"""Build the offline language choices from a pinned IANA registry snapshot.

The input must be the exact published text identified below; download it separately.
The catalog describes languages, not which languages any ASR model supports.
IANA and IETF affirm applicable registry rights under CC0 1.0, with the
third-party rights and warranty limitations in their published statement:
https://www.iana.org/help/licensing-terms
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE = "https://www.iana.org/assignments/language-subtag-registry/language-subtag-registry"
SOURCE_DATE = "2026-09-17"
SOURCE_SHA256 = "755fad43283be7b41ebe3c89ad054b6eaf928f404f9c0edb74799e0eab74beb1"


def build(data: bytes) -> dict:
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
        raise ValueError("IANA input does not match the pinned registry snapshot")
    text = data.decode("utf-8")
    if text.splitlines()[0] != f"File-Date: {SOURCE_DATE}":
        raise ValueError("IANA file date does not match the pinned registry snapshot")
    records = []
    for block in text.split("%%"):
        fields = {}
        last_key = None
        for line in block.splitlines():
            if line.startswith(" ") and last_key:
                fields[last_key][-1] += " " + line.strip()
            elif ": " in line:
                key, value = line.split(": ", 1)
                fields.setdefault(key, []).append(value)
                last_key = key
        if fields.get("Type") == ["language"]:
            records.append(fields)

    choices = {}
    for fields in records:
        code = fields.get("Subtag", [""])[0]
        if (
            not re.fullmatch(r"[a-z]{2,8}", code)
            or "Deprecated" in fields
            or fields.get("Scope") == ["special"]
            or not fields.get("Description")
        ):
            continue
        names = list(dict.fromkeys(fields["Description"]))
        choices[code] = {"code": code, "name": names[0], "aliases": names[1:]}

    # Deprecated codes with a unique replacement remain searchable, but are never
    # stored as new selections. Deprecated entries with no replacement are omitted.
    for fields in records:
        preferred = fields.get("Preferred-Value", [""])[0]
        if "Deprecated" not in fields or preferred not in choices:
            continue
        item = choices[preferred]
        old_code = fields.get("Subtag", [""])[0]
        for alias in [old_code, *fields.get("Description", [])]:
            if alias and alias != item["name"] and alias not in item["aliases"]:
                item["aliases"].append(alias)

    languages = sorted(choices.values(), key=lambda x: (x["name"].casefold(), x["code"]))
    languages = [choices["da"], choices["en"]] + [
        item for item in languages if item["code"] not in {"da", "en"}
    ]
    return {
        "source": SOURCE,
        "file_date": SOURCE_DATE,
        "source_sha256": SOURCE_SHA256,
        "license": "CC0-1.0",
        "license_url": "https://www.iana.org/help/licensing-terms",
        "languages": languages,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = json.dumps(
        build(args.registry.read_bytes()), ensure_ascii=False, separators=(",", ":")
    )
    args.output.write_text(output + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
