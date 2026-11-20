"""Fixed fictional DA/EN coverage probes; no model, file input or research-data accuracy claim.

Run from the repository root: python -m docs.examples.anonymisation_evaluation
The gold policy deliberately includes contextual identifiers and excludes a template email.
These choices exercise missed mentions/false suggestions; they are not an accepted policy.
"""

import json

from docs.examples import anonymisation_demo as demo


def _case(language, parts, known=()):
    text, gold = "", []
    for part in parts:
        if isinstance(part, str):
            text += part
        else:
            literal, category, cluster = part
            gold.append((len(text), len(text) + len(literal), category, cluster))
            text += literal
    return language, demo.synthetic_source(text), gold, known


def synthetic_cases():
    """Gold offsets come from literal assembly, including repeated emails and an emoji."""
    return [
        _case(
            "da",
            [
                "🙂 ",
                ("Fiktivia Testsen", "person", "da-person"),
                " skrev til ",
                ("da@example.invalid", "email", "da-email"),
                ".\r\n",
            ],
            ("Fiktivia Testsen",),
        ),
        _case(
            "da",
            [
                ("Fiktivia", "person", "da-person"),
                " fortalte om ",
                ("Fiktivias", "person", "da-person"),
                " arbejde i ",
                ("Fiktivby", "place", "da-place"),
                ".",
            ],
            ("Fiktivia",),
        ),
        _case(
            "da",
            [
                "Jeg er ",
                ("den eneste fiktive fyrpasser", "context", "da-role"),
                " i ",
                ("Fiktivby", "place", "da-place"),
                ".",
            ],
        ),
        _case(
            "en",
            [
                ("Fictional Testford", "person", "en-person"),
                " emailed ",
                ("en@example.invalid", "email", "en-email"),
                " and ",
                ("en@example.invalid", "email", "en-email"),
                ".",
            ],
            ("Fictional Testford",),
        ),
        _case(
            "en",
            [
                ("fictional testford", "person", "en-person"),
                " met ",
                ("Fictional Testfrod", "person", "en-person"),
                ".",
            ],
            ("Fictional Testford",),
        ),
        _case("en", ["The fictional email template is template@example.invalid."]),
    ]


def evaluate():
    """Score unique exact boundaries, ignoring detector category; print no source/cluster data."""
    report = {
        "scope": "fixed-synthetic-rules-only; not model or research-data accuracy",
        "ruleset": demo.RULESET,
        "languages": {},
    }
    for language in ("da", "en"):
        counts = {
            "cases": 0,
            "gold_mentions": 0,
            "exact_hits": 0,
            "false_suggestions": 0,
            "missed_mentions": 0,
            "cases_with_misses": 0,
        }
        clusters, missed_clusters, categories = set(), set(), {}
        for case_language, snapshot, gold, known in synthetic_cases():
            if case_language != language:
                continue
            predicted = {(hit["start"], hit["end"]) for hit in demo.suggest(snapshot, known)}
            expected = {(start, end) for start, end, _, _ in gold}
            missing = expected - predicted
            counts["cases"] += 1
            counts["gold_mentions"] += len(gold)
            counts["exact_hits"] += len(expected & predicted)
            counts["false_suggestions"] += len(predicted - expected)
            counts["missed_mentions"] += len(missing)
            counts["cases_with_misses"] += bool(missing)
            for start, end, category, cluster in gold:
                clusters.add(cluster)
                if (start, end) in missing:
                    missed_clusters.add(cluster)
                category_counts = categories.setdefault(category, {"gold": 0, "exact_hits": 0})
                category_counts["gold"] += 1
                category_counts["exact_hits"] += (start, end) in predicted
        report["languages"][language] = {
            **counts,
            "clusters": len(clusters),
            "clusters_with_misses": len(missed_clusters),
            "categories": categories,
        }
    return report


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=True, indent=2))
