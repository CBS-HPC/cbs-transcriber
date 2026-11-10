# 0128 — Reversible library list visibility

Proposed 3 November 2026. Extends ADR 0051.

The library chooser discovers earlier defaults as well as saved recent paths. A removed
entry therefore needs an explicit, durable suppression; deleting it from the recent list
alone would make the next discovery restore it. `hidden-libraries.json` in the app folder
holds only each hidden absolute path and a digest binding its directory and database file
identities. A replaced library is not accidentally hidden by an older decision. Each
non-current library offers **Remove from list**; it never changes that library, its
recordings, backups, jobs or remote resources. A visible **Hidden libraries** disclosure
always offers the hidden entries and **Show again**, so work is not forgotten. The active
library cannot be hidden. Opening an explicitly chosen hidden library makes it visible again.

Mutations validate the current discovery list and identity-bound token, hold the target's
normal server lease while hiding it, and serialize the bounded visibility file through an
OS lock and atomic replace. Acquiring the normal lease may create `server.lock` if absent.
Malformed visibility metadata fails open for discovery (show libraries) and closed for
mutations. This setting is presentation metadata, not authority to delete files or cancel
jobs; no cloud calls occur.

## Permanent library deletion remains separate

This change does **not** implement the requested permanent-delete action. Libraries can
refer to project folders outside their own directory, paths shared with another library,
and unresolved local or UCloud journals. A safe implementation needs an exact, bounded
preview distinguishing owned root files, separately managed projects and retained cloud
obligations; active-lease exclusion; explicit fingerprint-bound confirmation; and durable
same-volume staged deletion with crash recovery and a surviving cleanup record. It must
never unlink arbitrary parent/project paths, silently discard unresolved cloud work, or
turn an interrupted delete into a newly created empty library. Synthetic tests and a
separate platform qualification are needed before activating it.
