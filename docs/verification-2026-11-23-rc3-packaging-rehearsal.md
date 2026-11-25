# Local rc3 packaging rehearsal

23 November 2026, Windows. The rehearsal used a private scratch copy of `4e1c4b2`,
whose product bytes match the tested `34e8bc6` composition. It staged three release
identities in the copy, then used the existing uv 0.9.29 and verified cached build
tools. The rehearsal assets are separate from the planned pilot distribution.

## Build and packaged HTTP check

- uv regenerated and exported the lock offline. Parsed lock data matched the source
  lock except for the editable app's version (0.1.0 to 0.2.0rc3).
- Built the `py3-none-any` wheel and matching constraints. All 164 ZIP entries passed
  CRC and RECORD checks. Packaged app paths matched the staged source. The version
  and entry point were correct.
- Installed the wheel by itself into a scratch target, then used the existing tool
  Python's dependencies. From a separate fake home, a worker-free packaged app
  returned **MVP v2 rc3** through its HTTP test client. The served JavaScript and CSS
  matched exactly, and the rendered page matched after its normal profile and
  instance attributes were inserted. A synthetic project and recording-list read passed.
- Launched the packaged module on Windows with the same existing dependencies and a
  new test home, library and loopback port. The app served the expected profile and
  JavaScript/CSS bytes and saved a synthetic project. Ctrl+C exited cleanly and
  released the port. A second launch reopened the same project and shut down cleanly.
- Wheel SHA-256
  `4a84aea6d037b05e5acdd6586462b7c3893f93dcaf69955bf736e8a41d1d136c`.
  Constraints SHA-256
  `a5cee8943c142798b0000bcfb167bcdb807147ee5f57d8bb6b8652667eb17bad`.

## Fresh install evidence

The managed-only interpreter lookup refused offline because the machine lacked
cached managed Python 3.13. A second try used existing standalone CPython 3.13.5 to
create a fresh App virtual environment. uv resolved 136 locked packages and failed
when it needed the exact CPU Torch 2.8.0 wheel, whose Windows CPython 3.13 archive
was missing from the cache. The lock and hashes were retained.

A fresh install with every dependency, native shortcut activation, and managed
Windows or macOS acceptance are separate checks. The pilot commands keep App, Library and Projects together
under a separate test root.
