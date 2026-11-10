# Focused rc7 Save passage / Split comparison — 10 November 2026

The matched 1,800-passage measurements improve actual editing readiness from
16.36 to 8.20 seconds for Save passage and from 14.91 to 7.52 seconds for Split at
cursor. All six long rc7 observations still exceed the three-second goal. The
100-passage observations are slightly slower than rc5, while remaining below
0.85 seconds. These are three-sample observations, not a general latency guarantee.

The user accepts these improvements for MVP and requests further optimization
after MVP. This report records the immutable candidate's measurements; it does not
establish that the candidate has been loaded into the running app.

## Identity and matched protocol

- rc7 code: `60e07a9b8da178c0c6f1278b562dd518fc36d55c`; package:
  `2bfb635383d8a5f191c999f0883a6801d39efc81`.
- rc5 code: `89c196dabcbb52fe5d340338755fd99a6048c48b`; package:
  `6eb908cc0da860acdab63e62f78f13a2505baaf0`. See the
  [rc5 baseline audit](rc5-save-split-speed-2026-11-10.md) for its profiling details.
- All four runs report clean Git state and `source_unchanged_after_run: true`.
  Each package used its own verified source inventory.
- Windows 11 build 26100, Python 3.13.5, eight logical CPUs and Chromium
  156.0.8078.4, using the existing isolated developer environment.
- Separate processes for 100 and 1,800 passages; seed 11, fifteen Danish-like
  timed words per passage, three speakers, one open, three Save observations and
  three Split observations. Each disposable fixture has one recording and one
  result, ending with seven content versions.
- Both focused wrappers select exactly `save` and `structure`, with the latter
  restricted to Split. Their measurement code is identical: the wrapper diff
  changes only package/source labels, its run name and trailing whitespace. The
  underlying measurement wrapper has the same SHA-256 in every run.
- The editor is already open, its text changed or a valid caret positioned, and
  its button enabled before the action clock starts. Actual controls-ready means
  an enabled editor accepted an unsaved input probe; the probe is then cancelled
  and checked not to change saved content/revision.
- Background settlement includes that probe's requests. It is a perturbed upper
  bound on settlement, separate from the period during which editing is blocked.
  Render totals also cover the measured interval and are not necessarily all
  before controls-ready. API, render and background timings are not additive.
- The fixtures use normal producer paths, so generated canonical-validation and
  serialized-mirror binding facts may already be warm. Cold fallback latency was
  not measured. Large multi-recording projects, large histories and external
  providers were not measured. These runs use disposable synthetic libraries and
  a random loopback port, without private media, live library or provider work.

The preliminary `editing-save-split-rc7-100-nov10.json` run used an unmatched
protocol and is excluded. Only the four focused artifacts listed below support
this comparison.

## Actual editing readiness

Seconds: median (minimum–maximum), three observations per cell. The final column
counts observations over three seconds for rc5 / rc7.

| Passages | Action | rc5 controls-ready | rc7 controls-ready | Over 3 s |
| --- | --- | ---: | ---: | ---: |
| 100 | Save | 0.735 (0.668–0.749) | 0.773 (0.728–0.843) | 0/3 / 0/3 |
| 100 | Split | 0.654 (0.595–0.782) | 0.836 (0.811–0.850) | 0/3 / 0/3 |
| 1,800 | Save | 16.362 (9.625–16.928) | 8.197 (7.987–9.341) | 3/3 / 3/3 |
| 1,800 | Split | 14.910 (13.861–15.581) | 7.518 (7.345–11.065) | 3/3 / 3/3 |

## API, rendering and background settlement

Seconds: median (minimum–maximum), three observations per cell. Primary API is
the server's mutation-request duration. Rendering and background settlement are
reported separately from controls-ready above.

| Version | Passages | Action | Primary API | Render total | Background with probe |
| --- | --- | --- | ---: | ---: | ---: |
| rc5 | 100 | Save | 0.475 (0.469–0.520) | 0.096 (0.094–0.115) | 1.130 (1.087–1.171) |
| rc7 | 100 | Save | 0.446 (0.445–0.476) | 0.066 (0.056–0.068) | 1.032 (0.997–1.226) |
| rc5 | 100 | Split | 0.385 (0.375–0.548) | 0.094 (0.093–0.100) | 1.201 (1.106–1.260) |
| rc7 | 100 | Split | 0.502 (0.478–0.511) | 0.063 (0.058–0.069) | 1.346 (1.193–1.425) |
| rc5 | 1,800 | Save | 12.566 (6.515–13.144) | 1.745 (1.616–1.772) | 26.108 (16.353–26.119) |
| rc7 | 1,800 | Save | 4.990 (4.881–5.662) | 0.803 (0.760–0.815) | 12.828 (11.587–13.490) |
| rc5 | 1,800 | Split | 10.325 (9.745–11.366) | 1.961 (1.823–2.202) | 29.064 (24.863–29.201) |
| rc7 | 1,800 | Split | 4.796 (4.394–7.011) | 0.794 (0.641–0.983) | 13.954 (13.077–18.558) |

Every measured rc7 mutation caused one full transcript render, compared with two
in rc5. Long response sizes remain unchanged: Save median 4,938,930 bytes and
Split median 4,939,543 bytes, approximately 4.94 MB each. Short responses remain
approximately 268 KB. Full recording projections, response serialization and
synchronous project snapshots remain part of the operation.

## Changes, correctness and remaining work

The candidate reuses bounded canonical-format facts for successfully serialized
JSON-origin snapshots and bounded bindings between freshly hashed raw mirrors and
their canonical payload. Every later read still hashes actual bytes, and owner,
revision, Working-role and canonical fallback checks remain authoritative. It also
coalesces duplicate hint loads and avoids the second transcript redraw after
editing. No durability step is deferred or removed by these optimizations.

All recorded semantic checks are true in both versions: Save preserves retained
word intervals, leaves the appended word untimed and creates no enhancement job;
Split matches the exact production transform, and its unsaved-input probe preserves
the saved revision. Source verification also passes after every run. These checks
support the measured cases; they do not establish unrelated workflow acceptance.

Post-MVP work should target the remaining long-transcript API/projection and full
snapshot costs, the approximately 4.94 MB mutation response, and remaining browser
work, while retaining exact history, Working/CAS checks and durable snapshots.
Repeat the same readiness probe and separately measure cold fallback and larger
project/history cases before claiming that long editing meets the three-second goal.

## Evidence

Artifacts are in the developer's temporary results directory; they contain synthetic
measurements and were not added to the repository.

| Artifact | SHA-256 |
| --- | --- |
| `editing-save-split-rc5-100-nov10.json` | `d997b90ffe8ec9c99376157210cdfb093e3c2f66a3e3f3b475afc29613bccb07` |
| `editing-save-split-rc5-1800-nov10.json` | `d6763c0d74e8ab7a8cd5fe1ed4d9459018c1306c09d24d69c2a153a4d2101353` |
| `editing-save-split-rc7-focused-100-nov10.json` | `e37c6f7b7094beb43ee65679716117818658c046880928a2bb2dc576d04a7784` |
| `editing-save-split-rc7-focused-1800-nov10.json` | `a860309a473194c860cb0be5f6ccc24b7239cb7975036eaf33b014d866fd22f0` |

- Source inventory SHA-256, rc5:
  `0ff562436db78af89a59dbeb03e096be41afd902c9e0c364816925909020db5c`;
  rc7: `d3a951973ccd986fee2fc771e936d0bae3ca07cc8b7e153caca60fd32806bb62`.
- Focused wrapper SHA-256, rc5:
  `cdd7057bd024fc78aec6c5653228efb2ae6f683c242b237b1a16c59204b76c43`;
  rc7: `eec279515b9c51685607b95d2a6e23d2405d385a982bef63039432412e4fc04f`.
- Shared underlying wrapper SHA-256:
  `bb767a1922be181bfb4701922e62d1c91322baf4ddbadf46a6327042c60060b8`.
