# Supervisor startup failure — 27 October 2026

The reported four-file CBS Runtime failure was reproduced before any inference. The running
backend retained the singleton renderer from commit `4dfa01c`, while its supervisor template
was read from the newer working tree. That template introduced `__BATCH__`, which the loaded
renderer did not replace. Its existing fail-closed validation therefore raised exactly:

> The UCloud supervisor template has an unknown placeholder.

Read-only local diagnostics supplied by the coordinating chat place these jobs in schema 11,
without a frozen batch plan or shared batch ID, at progress 0.1. No allocation submission intent
or remote job ID was recorded. This separates the incident from the older native Transcriber
output-import failure and from shared batch controller/inference failures. It does not prove
that no public setup folders were created.

## Deterministic reproduction

The reproduction loaded only the historical `supervisor_script` function from Git and used
the current module's constants, current packaged template and synthetic UCloud settings.
It made no network requests and opened no recordings, credentials or real library database.

```python
import ast
import re
import subprocess
from cbs_transcriber import enhancement_api as api, providers

source = subprocess.run(
    ["git", "show", "4dfa01c:src/cbs_transcriber/enhancement_api.py"],
    check=True,
    capture_output=True,
    text=True,
).stdout
function = next(
    node
    for node in ast.parse(source).body
    if isinstance(node, ast.FunctionDef) and node.name == "supervisor_script"
)
namespace = dict(api.__dict__)
exec(
    compile(ast.Module(body=[function], type_ignores=[]), "historical-renderer", "exec"), namespace
)
settings = providers.Settings(
    provider_type="ucloud",
    base_url="https://synthetic.invalid",
    ucloud_transcriber={"storage_path": "/drive/synthetic", "project_id": "synthetic"},
)
keys = {
    node.value
    for node in ast.walk(function)
    if isinstance(node, ast.Constant)
    and isinstance(node.value, str)
    and re.fullmatch(r"__[A-Z0-9_]+__", node.value)
}
template_keys = set(re.findall(r"__[A-Z0-9_]+__", api.SUPERVISOR.read_text(encoding="utf-8")))
print(sorted(template_keys - keys))
print(bool(api.supervisor_script(settings, "synthetic", False)))
namespace["supervisor_script"](settings, "synthetic", False)
```

Run using the repository environment with `uv run python -` and the script on standard input.
Observed exit status: **1**, after printing `['__BATCH__']`, then `True`, then the exact
unknown-placeholder exception. The current renderer already handles that token correctly.
No production renderer or template change was needed for this diagnosis. The placeholder
check remains intact; a hypothetical path-related failure was not used to justify a change.

## Regression and limits

`tests/test_supervisor_rendering.py` exercises the real packaged template in singleton/batch
and prepared/preparation modes. It verifies token completion, mode/generation/path selection,
normalized line endings and pinned hashes, then checks Bash syntax when a suitable local Bash
exists. A separate case confirms that a genuinely unknown template token still fails closed.
Focused result: **5 passed**; Ruff lint/format passed. The installed Git Bash was found and
its syntax check ran for all four mode combinations.

Activation belongs to the coordinating chat: preserve the library backup and idle/lease checks,
start Python and its packaged assets from the same version, and verify the running contract
before retrying jobs. This diagnosis did not restart the backend, migrate a real library, send
private inputs, download weights, create a live allocation or retry an existing recording.

A separate current-source controller/coordinator run reported **70 passed, 1 skipped and 2
failed**. Both failures were an eight-second wait for synthetic worker acceptance in the late
join and shutdown/recovery tests. The peer was changing coordinator test timing/instrumentation
during that run; those timing failures do not reproduce the supervisor placeholder exception.
An immediate isolated rerun of those exact two cases passed (**2 passed in 17.48 seconds**).
They remain sensitive to concurrent load/test timing and were not changed by this diagnosis.
