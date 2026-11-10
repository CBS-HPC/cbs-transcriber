# Existing sole-result Working designation: review checkpoint

The user wants a recording with exactly one untouched saved transcript version to
have that version set as Working, including eligible recordings that already exist.
The existing-recording portion is **unfinished**. Do not activate the isolated
`codex/working-sole-existing` experiment (`d952453`, `82ba2ff`); independent
review found the blockers below. The commits have not been merged or loaded.

ADR 0133 already provides automatic Working assignment for a first newly created
result when the authorized creation path establishes that there was no earlier
result or Working decision. Fresh processing successors use frozen authority and
role revisions. Legacy jobs and historical observations do not gain authority
merely by being replayed or restored. These guarantees do not automatically mark
old unassigned single-result recordings.

## Blockers and next steps

- Prove eligibility from an intact single creation version and current recording
  and result projections, while respecting existing role events, explicit clear,
  Trash, multiple results or versions, edited baselines, pending jobs and frozen
  authorizations. A `legacy_observed` snapshot alone cannot prove an untouched source.
- Protect restored libraries, including imported results without a job. Supported
  complete restores retain a `preservation` tree, but an off-app copy or manually
  removed evidence cannot reliably be distinguished from an ordinary library.
  Check evidence without treating permission errors or unsafe links as absence.
- Define a bounded startup/admission strategy and measure large job histories. The
  isolated experiment scanned jobs once per 16 candidates, still scaling with both
  history and candidate pages; its small synthetic timing is not a capacity bound.
- Do not automatically regenerate `project.json`, `.pending` files or exported
  text solely from an external `storage_dir`. Prove ID ownership, containment,
  reparse-free parents and each output target, and preserve unrelated existing files.
  Legacy projects without a storage directory need no snapshot warning.
- Design a durable, promotion-specific way to recover any snapshot update interrupted
  after the database commits, without repeatedly rewriting unrelated projects. Any
  new persistence marker must be covered by backup, restore, library switching,
  deletion, crash recovery and concurrent operations. Alternatively, explicitly
  retain and expose pending reconciliation with an equally safe recovery path.
- Cover exact role concurrency, restored imports, unowned/missing/symlink/reparse
  paths, retained files, failed snapshot updates, process crashes and large histories
  using synthetic libraries; then obtain independent review before real activation.
