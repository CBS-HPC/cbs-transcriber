# 0166 — Keep manual wording and splitting independent of word timing

Accepted 10 November 2026. The user explicitly requires manual text changes and valid
between-word splits to remain available regardless of word-timing quality. Split inside a
word is disabled as a text-boundary rule. This supersedes ADR 0156 for fresh manual actions,
while retaining that generation exactly for existing history and clients.

Fresh Text PATCH edit-v3 records permissive-word-map-v2. Keep verified unchanged word
dictionaries/times and uniquely corresponding replacement intervals. A literal prefix append
keeps the verified prefix even when the last word repeats. New, ambiguous or unverified words
have null times; mapping/budget limitations produce a nonblocking warning. Never infer timing
or automatically realign. Request shape/size, revision, ownership and Working safeguards remain.

Fresh split-v2 records manual-word-boundary-v2. The exact code-point cursor must sit beside
whitespace with nonempty halves; both browser and server enforce this independently of timing.
Retain the existing split transform when its proof is valid. Otherwise preserve independently
verified whole words, null uncertain pieces and assign half bounds only from complete verified
coverage or a valid explicit interior audio point. Unlocated halves remain unlocated; existing
Replay can identify its full-recording fallback rather than claim a guessed half interval.

The exact predecessor retains earlier originals and bounds. Undo recomputes using the recorded
policy, replaces only its random right ID and proves exact current halves before restoring.
Absent policy keeps legacy split behavior; unknown policies cannot grant an inverse. Existing
edit-v2/split routes and strict conservative functions remain available unchanged. Repin the
captured transcript validator when its source changes; do not reinterpret historical versions.

This changes manual behavior, not storage layout or speed targets. Further speed optimization
is deferred after MVP at the user's request.
