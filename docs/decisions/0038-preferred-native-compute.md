# 0038 — Preferred native B200 compute

Accepted: 22 October 2026. Extends 0028 and 0035.

Superseded by [0039 — Fixed compute and model-based settings](0039-fixed-compute-model-settings.md).

The user selected **gpu-nvidia-b200-1-gpu** as the default for native Transcriber. During
read-only auto-setup, first validate application backend support, GPU metadata, project
ownership and funding as before. Among eligible products, resolve an exact unique product
ID match automatically. Keep the complete provider/category/product identity in the setup
and frozen job; the display name alone does not identify an allocation.

If matching products differ by provider/category, require an explicit named choice. If
the preferred product is absent, explain that fact and require an explicit alternative,
even when only one other machine is eligible. Host RAM does not select a fallback or imply
GPU memory. Metadata discovery does not prove execution/model fit or allocate any compute.

Explicit saved/current selections and template-derived machines retain priority and must
still pass compatibility/funding validation. No existing settings, frozen jobs, storage
locations or credentials are rewritten. UCloud vLLM and enhancement runners are unaffected.

Mocked discovery/API checks cover mixed GPU catalogs, exact default selection, unavailable
and unfunded machines, ambiguous full identities and explicit/template preservation.
