# 0018 — UCloud execution of the enhancement worker

Accepted, 20 October 2026. Extends 0016/0017; independent of native Transcriber and vLLM.

The released UCloud API project supports SSH-enabled template jobs and remote Python workloads.
Its high-level runner always submits anew and terminates on exit, so it cannot own our durable
reconnect/cancel lifecycle. Reuse its client, specification builder and SSH endpoint parser;
coordinate the journal in the existing enhancement queue. Never call Settings.from_env().

Keep one separate UCloud enhancement configuration (server/project/template, remote Python and
working folder, optional existing local SSH config/key). No serving endpoint is required. It
defaults to permitted for Protected projects, preserving explicit denials. Freeze this identity
in each job and recheck current approval/configuration before private uploads and retries.
Save the token in the existing server-scoped credential store; never send control/HF tokens
through SSH arguments, job specifications or input bundles. The remote runtime/models must be
prepared beforehand; this runner does not install packages or fetch weights.

Checkpoint submission intent before allocating and the remote ID before any private transfer.
An uncertain allocation is never automatically repeated. Retry reconnects to the same owned job;
if its allocation has ended, the user must start a new enhancement. A detached, locked remote
wrapper avoids duplicate processes after an uncertain SSH response and retains stage/window
checkpoints. App shutdown leaves the owned remote allocation running for explicit reconnect.
Explicit cancellation terminates only its recorded job. Success downloads required stage JSON
before requesting termination. Failures retain the job for retry/recovery within its time budget.

Use noninteractive bounded OpenSSH/SCP subprocesses with curated errors and discarded stderr.
Use an app-owned known-hosts file with accept-new checking; never disable host-key checking or
rewrite the user's SSH configuration. Validate API-provided host/user/port and quote remote argv.
Only fixed filenames inside the run-owned directory are transferred. Validate downloaded output
through the same source-preserving merge as local execution; append one independent result.

Review gates: retain template product/resources; reject native Transcriber templates; freeze
source/configuration; no opaque command or exception output in API/logs; no replay of uncertain
submission; no cleanup of unowned jobs or remote folders; do not imply a metadata check proves
SSH/runtime/model readiness. Automated tests mock the control plane and transport; live execution
is separate acceptance work, not established by those tests.

Source: [UCloud API v0.1.9 Python/SSH reference](https://github.com/CBS-HPC/ucloud-api/blob/v0.1.9/docs/reference/api.md).
