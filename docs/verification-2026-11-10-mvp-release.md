# MVP 0.1.0 release verification — 10 November 2026

The user accepts the rc7 editing speed for MVP and explicitly defers further optimization.
The subsequent release adds timing-independent manual Text/split policies, a versioned uv
wheel/constraints installation route and installed-package shortcuts. Spelling and parked
Runtime/anonymisation/cloud-backup work remain outside the MVP.

## Manual editing

Fresh edit-v3 records permissive-word-map-v2; fresh split-v2 records manual-word-boundary-v2
([ADR 0166](decisions/0166-timing-independent-manual-editing.md)). Existing strict functions
and routes retain their exact behavior for old clients/history. Uncertain words become
explicitly untimed rather than blocking wording. Split is enabled only beside whitespace,
with nonempty saved-text halves; missing timing does not block it or authorize guessed bounds.

The assembled backend gate passed **258 tests**, including new-policy Working/revision fences,
Undo and repinned contained history. Independent reviewers gave Go; three additional stale
Working/unknown-policy tests and twenty pure-function adversarial assertions passed. All 37
pre-existing transcript function bodies are unchanged. The captured source is 65,313 normalized
bytes, within its existing 64 KiB cap, and its new source pins agree.

The broader browser run passed 31 cases and exposed ten older expectations: no-op Save was
still clicked despite its disabled contract; conservative rewrites expected all timing removed;
a split test expected mandatory paused playback; a Cancel selector matched a hidden timing
panel as well; and an MVP-only ordinary-save case used the full profile with saved realignment.
Those test contracts are corrected without changing production behavior. The targeted rerun
passed **11 cases** in 73.67 seconds; all **six** other Text cases were rerun under their corrected
MVP/full profile scopes and passed in 34.79 seconds. Independent review confirmed the corrected
oracles retain literal-data, timing, draft, Working and no-job assertions. This layered result
does not describe the initial broader run as green. Ruff checks pass across the repository;
all 821 Python files pass the format check.

## Wheel installation

With uv 0.9.29 and an existing Windows x64 CPython 3.13.5, a fresh isolated uv tool environment
installed **135 packages**. All 134 runtime dependency versions match the platform-selected
locked export, and the observed UCloud/CPU Torch source URLs match the lock. Direct-URL
metadata did not retain archive hashes, so this check does not claim compressed-artifact hash
observations or complete equivalence to uv sync --locked.

An offline attempt first stopped on an uncached CPU-wheel URL. The subsequent network-enabled
installation retrieved Python packages only; no provider job or ASR/speaker-model fetch was
started. It did not synchronize the serving environment. The installed wheel, independently
of source imports, passed the existing synthetic core-startup, bundled FFmpeg/media preparation,
transcript import/export and playback-byte-range checks. Credentials were not accessed.

The companion constraints generator passed 33 focused cases and independent review. It keeps
non-Torch export lines, emits six exact supported-platform CPU wheel URLs with SHA-256 fragments,
and refuses unsupported target resolutions. It validates special Torch mappings, not an
arbitrary pair's complete export/lock equivalence; generation uses one frozen committed source.
The tool installer resolves its own environment and does not consume the repository lock.

Installed-package shortcuts passed 37 focused cases and independent review. A native Windows
COM creation check used only a disposable tool folder, verifying the exact installed interpreter,
MVP arguments, working directory and collision refusal. No real Desktop/Start menu entry was
created or launched. Real clean-machine, Mac, inference/GPU, OS-keychain and launcher-click
acceptance remain separate.

## Publication boundary

The local hygiene audit found no high-confidence credential/private-recording leak. The user
chooses a clean public repository snapshot, with the old development history/private reference
bundle retained privately. CBS font embedded terms allow CBS/collaborator distribution; its
separate notice and other third-party attribution remain. No broad app licence is invented.

Private detailed receipts remain in Temp: manual-timing-assembled-backend-nov10.xml,
manual-timing-assembled-browser-nov10.xml, manual-integration-guard-review-nov10.xml,
cbs-wheel-tool-preflight-smoke-nov10.json, cbs-wheel-tool-preflight-resolution-nov10.json,
cbs-wheel-tool-shortcut-preflight-nov10.json and cbs-publication-hygiene-2026-11-10.md.

## Remaining acceptance

Native same-allocation existing-output recovery and owned-folder Trash cleanup passed the
user's rc6 acceptance. Genuinely missing-output resubmission, live early retrieval, remote
shutdown/relaunch handoff, real Mac/clean-device installations and NVivo imports remain open.
The older Working tests initially constructed current-schema fixtures for a schema-18-only
operation. Their corrected genuine-schema-18 fixtures and explicit schema-19 refusal check
pass all **27** cases without changing the production guard. Old eligible sole results in a
current schema-19 library may still require explicit Set as working. The focused release gates
do not claim a fresh all-repository test-suite pass. Further Save/Split speed work and spelling
are post-MVP.
