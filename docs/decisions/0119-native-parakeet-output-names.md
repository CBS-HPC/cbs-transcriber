# 0119 — Recover native Parakeet outputs without a language suffix

The UCloud Transcriber Batch service returned successful files named
`<input-id>_parakeet.json`, without a detected-language suffix. The previous batch
lookup and native artifact validator rejected this exact form before importing any
transcript. The submitted model parameter for the affected batch was `parakeet`.

Accept this additional exact filename only when the frozen submitted model is
`parakeet`. Match the exact input identity and owned output folder, and continue
to refuse missing, mismatched or multiple candidate outputs. A filename without
a language suffix does not establish a detected language. Apply the same rule to
download selection, native artifact verification and the offline MIG checker.

Retry continues to reconnect to the recorded allocation, preserving successful
members and raw output evidence; it does not submit another UCloud job. Cleanup
still requires validated durable results for all members. Older output names and
their existing checks remain valid.
