"""Whole-transcript wording alignment. Never edits text or invents audio timing."""

import re
import unicodedata
from bisect import bisect_left
from collections import Counter, defaultdict
from difflib import SequenceMatcher

WORD = re.compile(r"\w[\w\u0300-\u036f]*")


def tokenize(segments):
    return [
        (unicodedata.normalize("NFKC", match[0]).casefold(), passage, match.start(), match.end())
        for passage, segment in enumerate(segments)
        for match in WORD.finditer(segment["text"])
    ]


def token_pairs(left, right, anchor=None):
    """Use stdlib global sequence matching; frequent fillers are not primary anchors."""
    words_left, words_right = [t[0] for t in left], [t[0] for t in right]

    def pairs(a, b, offset_left=0, offset_right=0):
        if a == b:
            return [(offset_left + i, offset_right + i) for i in range(len(a))]
        if not set(a).intersection(b):
            return []
        if min(len(a), len(b)) > 500:
            # ponytail: unique phrase anchors bound work; stdlib still aligns the gaps.
            def unique_triples(words):
                found = {}
                for index in range(len(words) - 2):
                    key = tuple(words[index : index + 3])
                    found[key] = None if key in found else index
                return found

            first, second = unique_triples(a), unique_triples(b)
            candidates = sorted(
                (i, second[key])
                for key, i in first.items()
                if i is not None and second.get(key) is not None
            )
            tails, ends, previous = [], [], []
            for index, (_, target) in enumerate(candidates):
                position = bisect_left(tails, target)
                previous.append(ends[position - 1] if position else -1)
                if position == len(tails):
                    tails.append(target)
                    ends.append(index)
                else:
                    tails[position], ends[position] = target, index
            if ends:
                chain, current = [], ends[-1]
                while current != -1:
                    chain.append(candidates[current])
                    current = previous[current]
                matched, x, y = [], 0, 0
                for start_left, start_right in reversed(chain):
                    if start_left < x or start_right < y:
                        continue
                    matched.extend(
                        pairs(a[x:start_left], b[y:start_right], offset_left + x, offset_right + y)
                    )
                    matched.extend(
                        (offset_left + start_left + i, offset_right + start_right + i)
                        for i in range(3)
                    )
                    x, y = start_left + 3, start_right + 3
                matched.extend(pairs(a[x:], b[y:], offset_left + x, offset_right + y))
                return matched
        # autojunk avoids quadratic work on long runs of repeated interview fillers.
        matcher = SequenceMatcher(None, a, b, autojunk=True)
        return [
            (offset_left + x + i, offset_right + y + i)
            for x, y, length in matcher.get_matching_blocks()
            for i in range(length)
        ]

    if anchor is None:
        return pairs(words_left, words_right)
    a, b, length = anchor
    if (
        length < 1
        or length > 100
        or min(a, b) < 0
        or a + length > len(left)
        or b + length > len(right)
        or words_left[a : a + length] != words_right[b : b + length]
    ):
        raise ValueError(
            "The selected wording anchor no longer matches both results. Find it again."
        )
    return (
        pairs(words_left[:a], words_right[:b])
        + [(a + i, b + i) for i in range(length)]
        + pairs(words_left[a + length :], words_right[b + length :], a + length, b + length)
    )


def phrase_occurrences(segments, phrase):
    tokens = tokenize(segments)
    needle = [m[0] for m in tokenize([{"text": phrase}])]
    if not needle or len(needle) > 100:
        raise ValueError("Enter a phrase of 1–100 words.")
    words = [t[0] for t in tokens]
    occurrences = []
    for index in range(len(words) - len(needle) + 1):
        if words[index : index + len(needle)] == needle:
            segment = segments[tokens[index][1]]
            start = max(0, tokens[index][2] - 40)
            occurrences.append(
                {
                    "index": index,
                    "length": len(needle),
                    "passage": segment["id"],
                    "context": segment["text"][start : start + 180],
                }
            )
            if len(occurrences) == 200:
                break
    return occurrences


def compare_transcripts(left, right, anchor=None):
    tokens_left, tokens_right = tokenize(left), tokenize(right)
    pairs = token_pairs(tokens_left, tokens_right, anchor)
    lengths_left = Counter(t[1] for t in tokens_left)
    lengths_right = Counter(t[1] for t in tokens_right)
    counts_left = Counter(t[0] for t in tokens_left)
    counts_right = Counter(t[0] for t in tokens_right)
    matched_pairs = set(pairs)
    triples_left = Counter(
        tuple(t[0] for t in tokens_left[i : i + 3]) for i in range(max(0, len(tokens_left) - 2))
    )
    triples_right = Counter(
        tuple(t[0] for t in tokens_right[i : i + 3]) for i in range(max(0, len(tokens_right) - 2))
    )
    distinctive_tokens = {
        a
        for a, b in pairs
        if counts_left[tokens_left[a][0]] == counts_right[tokens_right[b][0]] == 1
        or (
            (a + 1, b + 1) in matched_pairs
            and (a + 2, b + 2) in matched_pairs
            and triples_left[tuple(t[0] for t in tokens_left[a : a + 3])]
            == triples_right[tuple(t[0] for t in tokens_right[b : b + 3])]
            == 1
        )
    }
    edges = defaultdict(list)
    for a, b in pairs:
        edges[tokens_left[a][1], tokens_right[b][1]].append((a, b))
    parents = {}

    def find(node):
        parents.setdefault(node, node)
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    accepted = []
    for (i, j), matches in edges.items():
        count, shortest = len(matches), min(lengths_left[i], lengths_right[j])
        forced = anchor is not None and any(
            anchor[0] <= a < anchor[0] + anchor[2] and anchor[1] <= b < anchor[1] + anchor[2]
            for a, b in matches
        )
        distinctive = any(a in distinctive_tokens for a, _ in matches)
        phrase = any(
            (a + 1, b + 1) in matched_pairs
            and a in distinctive_tokens
            and tokens_left[a + 1][1] == i
            and tokens_right[b + 1][1] == j
            for a, b in matches
        )
        if forced or (
            distinctive and (phrase or count >= min(3, shortest) and count / shortest >= 0.55)
        ):
            parents[find((0, i))] = find((1, j))
            accepted.extend(matches)

    components = defaultdict(lambda: [[], []])
    for side, index in list(parents):
        components[find((side, index))][side].append(index)
    groups = sorted((min(a), max(a), min(b), max(b)) for a, b in components.values() if a and b)
    # Dominant token links avoid one-word boundary spill joining an entire interview.
    # Overlapping extents need a contiguous display group, preserving every passage.
    merged = []
    for a, z, b, y in groups:
        if merged and (a <= merged[-1][1] or b <= merged[-1][3]):
            x, old_z, q, old_y = merged[-1]
            merged[-1] = (x, max(z, old_z), q, max(y, old_y))
        else:
            merged.append((a, z, b, y))

    rows, confidence, cursor_left, cursor_right = [], [], 0, 0

    def row(a, b, matched=False):
        rows.append(
            {"left": [s["id"] for s in a], "right": [s["id"] for s in b], "matched": matched}
        )

    def gaps(a, b):
        if a:
            row(a, [])
            confidence.append("unmatched")
        if b:
            row([], b)
            confidence.append("unmatched")

    matched_counts = Counter(tokens_left[x][1] for x, _ in accepted)
    for a, z, b, y in merged:
        gaps(left[cursor_left:a], right[cursor_right:b])
        row(left[a : z + 1], right[b : y + 1], True)
        count = sum(matched_counts[i] for i in range(a, z + 1))
        total = sum(lengths_left[i] for i in range(a, z + 1)) + sum(
            lengths_right[i] for i in range(b, y + 1)
        )
        confidence.append("strong" if count * 2 / max(1, total) >= 0.7 else "partial")
        cursor_left, cursor_right = z + 1, y + 1
    gaps(left[cursor_left:], right[cursor_right:])

    def wording(segments, tokens, matched_indexes):
        spans = defaultdict(list)
        for index, (_, passage, start, end) in enumerate(tokens):
            equal = index in matched_indexes
            runs = spans[segments[passage]["id"]]
            if runs and runs[-1]["equal"] == equal:
                runs[-1]["end"] = end
            else:
                runs.append({"start": start, "end": end, "equal": equal})
        return dict(spans)

    return {
        "rows": rows,
        "confidence": confidence,
        "wording": {
            "left": wording(left, tokens_left, {a for a, _ in accepted}),
            "right": wording(right, tokens_right, {b for _, b in accepted}),
        },
    }


def align_passages(left, right):
    """Keep the original row-only interface for local callers."""
    return compare_transcripts(left, right)["rows"]
