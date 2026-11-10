# 0035 — Token-associated project defaults

Accepted 22 October 2026. Extends ADR 0028's token-first discovery; no job, credential or
library persistence change.

When a new setup has several eligible projects, prefer the project associated with the
supplied native UCloud token when UCloud returns verifiable metadata. A recognized
`uc<hex-id>-...` prefix supplies only a record identifier. Read token metadata with the
released client's browse method and match that exact decimal ID, including pagination.
Require one unique record with a nonempty owner project that also appears in the eligible
project listing. Never infer a project or permission from the secret or record identifier.

This association selects a default; token ownership is not proof of permission scope.
Explicit saved/advanced project selections remain unchanged and must still be eligible.
Personal tokens, unrecognized references, absent records, and unsupported/forbidden token
metadata retain the named project chooser. Authentication failures, outages and malformed
or duplicate records remain errors. A failed later metadata page cannot yield a partial
ownership decision. Setup still performs no allocation, upload or credential write.

Use UCloud's documented frontend/RPC route convention for application groups:
`GET /api/hpc/apps/retrieveGroups`. A search hit remains only the default flavor; inspect
available siblings and pin the selected Batch identity. Tests must return 404 for the
incorrect `/apps/groups` path rather than mirroring an invented route.

Errors identify a static setup stage and optional HTTP status. Never return upstream
response bodies, token strings, or request/query values in failure diagnostics.

Verified upstream revision `c216a062878e88c6b07a416bd754a1e899d85b5d`:

- [Token creation and project ownership](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/core2/pkg/orchestrator/api_tokens.go)
- [Token resource metadata](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/shared/pkg/orchestrators/api_tokens.go)
- [Frontend route conventions](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/frontend-web/webclient/app/Authentication/DataHook.ts)
- [Application group RPC](https://github.com/SDU-eScience/UCloud/blob/c216a062878e88c6b07a416bd754a1e899d85b5d/provider-integration/shared/pkg/orchestrators/app_catalog.go)
