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

## Published package and activation

The clean public root is `5cf2e64d5fe5980586f9ec3d170b216eb1f98384`, tagged v0.1.0.
It has no parent commits; the earlier repository remains private. The public repository has
only main. The release was published on 10 November at 12:41 UTC. All six assets were then
downloaded anonymously and matched their local hashes and lengths. The wheel SHA-256 is
`57a9f2071aeef3b4d6b9079399c2f4826f0a610278f7ee68af5a1343341824fc`;
the constraints SHA-256 is
`b4e812858ed094d099403f9f2703d8f391e1959ca416ba4bdd79f3bd686dd835`.
Documentation corrections do not replace that wheel, constraints, or tag.

MVP 0.1.0 was activated from the frozen wheel-build source using the existing serving
interpreter without synchronizing it. The profile/label and four served assets matched the
release wheel exactly. Online/offline/post comparisons preserved every database table and
all 24 originals; the offline/post database hashes were identical. This verifies activation
and preservation, not a clean tool installation.

The user's subsequent per-user uv-tool install initially failed with Windows Cloud Files
hardlink error 396. A new uv 0.13.7 binary was installed, but PATH still selected Conda's
0.9.29 first. Explicit copy mode avoids the hardlink operation; the earlier isolated Windows
preflight also used copy mode. The user reports package installation succeeded after retrying.
Starting it then correctly refused the library already leased by the development server.
That server was stopped normally after confirming zero active jobs, batches and exports;
the user's installed app subsequently responded with the MVP 0.1.0 label, using its uv-tool
interpreter. Functional acceptance remains pending. The current guide
checks existing uv/PATH, explains per-user Python, uses `--link-mode copy`, and explains the
single-library-process rule. This is a documentation follow-up, with no runtime code change.

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
