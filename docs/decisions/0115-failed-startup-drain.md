# 0115 — Drain partial processing startup before releasing the library lease

Accepted 30 October 2026. Refines the queue and application lifespan without changing
database schema, frozen jobs or remote control authority.

A worker thread or later processing observer can fail to start after earlier workers are
already running. The previous startup path could release the serving library lease while
those accepted workers remained alive. A separate read-only schema probe could also leave
its SQLite connection open. Synthetic failure injections reproduced both cases.

Close schema-probe connections on every outcome. Enclose processing initialization within
lifespan cleanup and drain every started worker under the serving lease if startup fails.
Join all started workers before attempting runtime closes; attempt all closes even if one
fails. Preserve the original startup error while retaining cleanup error context. Never join
an unstarted thread or confuse an unrelated handled exception with a lifespan failure.

Deferred local-delete recovery may call initialization again in the same serving app. Refuse
restart while any prior worker is alive; after complete drain reset stop/cancel events before
starting fresh workers. Do not infer a new remote submission or change any durable job.

Synthetic startup and recovery tests, schema-boundary preservation and Windows process-lock
checks cover this contract. Clean-machine macOS and real storage-crash acceptance remain separate.
