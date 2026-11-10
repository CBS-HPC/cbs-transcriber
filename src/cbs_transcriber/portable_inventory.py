"""Portable managed-file policy; filenames never confer filesystem ownership.

The caller resolves database-owned roots. This module classifies relative names only; it
does not follow a path supplied by a result, worker checkpoint or backup manifest.
"""

import re

POLICY = "portable-files-v1"
ID = r"[a-f0-9]{32}"
STAGES = {
    "request.json": "processing_input",
    "audio.wav": "processing_input",
    "audio.input.json": "processing_evidence",
    "original.input.json": "processing_evidence",
    "audio.preparation.json": "processing_evidence",
    "outcome.json": "processing_evidence",
    "audio.f32.source.json": "processing_evidence",
    "producer-origin.json": "processing_evidence",
    "transcription.json": "processing_output",
    "retranscription.json": "processing_output",
    "alignment.json": "processing_output",
    "diarization.json": "processing_output",
    "alignment_language.json": "processing_evidence",
    "windows.json": "processing_checkpoint",
    "repair-run.json": "processing_evidence",
    "repair-status.json": "processing_evidence",
    "repair-upload.sha256": "processing_evidence",
    "progress.json": "processing_checkpoint",
    "done.json": "processing_checkpoint",
    "error.json": "processing_checkpoint",
    "cloud-doctor.json": "processing_evidence",
    "cloud-status.json": "processing_checkpoint",
}
CONTROL = {
    "batch.json",
    "membership.json",
    "heartbeat.json",
    "seal.json",
    "stop.json",
    "accepted.json",
    "inputs.json",
    "ready.json",
    "started.json",
    "failure.json",
    "outcome.json",
    "batch-status.json",
    "supervisor.json",
    "doctor.json",
    "cache.json",
    "transcription.json",
    "retranscription.json",
    "alignment.json",
    "diarization.json",
    "request.json",
    "audio.wav",
    "alignment_language.json",
}
EXCLUDED_NAMES = {
    "audio.mp3",
    "audio.m4a",
    "video.mp4",
    "waveform.json",
    "audio.f32",
    "audio.npy",
    "server.lock",
    ".in-use",
    "stdout.log",
    "stderr.log",
    "worker.log",
}


def recording_role(relative, *, original, job_ids, attempts=None):
    """Return a role or None for a known regenerable/nonportable managed child.

    Unknown names are refused so a newly introduced producer cannot silently disappear from
    a supposedly complete backup. Credential/config/script files are never candidates.
    """
    if relative == original:
        return "original"
    parts = relative.split("/")
    name = parts[-1]
    if (
        name in EXCLUDED_NAMES
        or name.endswith((".pending", ".tmp", ".lock", ".py", ".sh", ".log"))
        or ".pending." in name
        or name in {".env", "config.json", "credentials.json", "known_hosts"}
    ):
        return None
    if len(parts) == 2 and parts[0] == "sources" and re.fullmatch(ID + r"\.(json|csv)", name):
        return "import_source"
    match = re.fullmatch(r"ucloud-(" + ID + r")\.json", relative)
    if match and match[1] in job_ids:
        return "provider_response"
    match = re.fullmatch(r"ucloud-(" + ID + r")\.request-[0-9]{4}\.json", relative)
    if match and match[1] in job_ids:
        return "processing_input"  # Credential-free native submission descriptors.
    if len(parts) >= 3 and parts[0] in {"chunks", "enhancements"} and parts[1] in job_ids:
        tail = parts[2:]
        if parts[0] == "chunks" and len(tail) == 1:
            if re.fullmatch(r"response-[0-9]{4}\.json", name):
                return "provider_response"
            if re.fullmatch(r"request-[0-9]{4}-[0-9]{4}\.json", name):
                return "processing_input"  # Credential-free API request descriptors.
            if name == "plan.json":
                return "processing_evidence"
            if re.fullmatch(r"(?:(?:part|chunk)-)?[0-9]+\.(?:wav|mp3|m4a)", name):
                return None
        if parts[0] == "enhancements":
            if (
                len(tail) == 3
                and tail[0] == "attempts"
                and re.fullmatch(r"[1-9][0-9]{0,9}", tail[1])
                and int(tail[1]) < 2**31
                and attempts is not None
                and int(tail[1]) in attempts.get(parts[1], set())
                and name
                in {
                    "transcription.json",
                    "retranscription.json",
                    "alignment.json",
                    "diarization.json",
                    "outcome.json",
                    "audio.preparation.json",
                }
            ):
                return STAGES[name]
            if len(tail) == 3 and tail[0] == "units" and re.fullmatch(r"[0-9]{1,5}", tail[1]):
                if name in STAGES:
                    return STAGES[name]
                if name == "alignment.environment.json":
                    return "processing_evidence"
            if len(tail) == 1:
                if name in STAGES:
                    return STAGES[name]
                if name in {
                    stage + ".environment.json"
                    for stage in ("transcription", "alignment", "diarization")
                }:
                    return "processing_evidence"
                if re.fullmatch(r"(?:recognition|aligned)-[0-9]{5}\.json", name):
                    return "processing_checkpoint"
                if name.endswith(".invalid.json") and name[:-13] + ".json" in STAGES:
                    return "processing_evidence"
            if len(tail) == 2 and re.fullmatch(r"[0-9]{1,5}", tail[0]) and name in STAGES:
                return STAGES[name]
            if (
                len(tail) == 3
                and tail[0] == "passages"
                and re.fullmatch(r"[0-9]{5}", tail[1])
                and name in STAGES
            ):
                return STAGES[name]
            if re.fullmatch(r"repair-bundle-[0-9]{6}", tail[0]):
                if len(tail) == 2 and name in {"manifest.json", "COMMITTED.json"}:
                    return "processing_evidence"
                if (
                    len(tail) == 4
                    and tail[1] == "units"
                    and re.fullmatch(r"[0-9]{5}", tail[2])
                    and name in {"request.json", "audio.wav"}
                ):
                    return "processing_input"
            if tail[0] in {"repair-code", "repair-bundle"}:
                # Code is reproducible. Clip bundles are reconstructed from originals;
                # their validated receipt and outputs stay in the owner journal.
                return None
            if (
                len(tail) == 3
                and tail[0] == "repair-results"
                and re.fullmatch(r"[0-9]{5}", tail[1])
                and name in CONTROL
            ):
                return "processing_output"
            if (
                len(tail) == 3
                and tail[0] == "repair-artifacts"
                and re.fullmatch(r"[0-9]{5}", tail[1])
                and re.fullmatch(r"[a-f0-9]{64}\.json", name)
            ):
                return "processing_evidence"
    raise ValueError(
        "A managed recording contains a file unsupported by the portable inventory policy."
    )


def library_role(relative, *, runtime_ids, restore_ids):
    parts = relative.split("/")
    if len(parts) < 3:
        raise ValueError("The portable library evidence path is unsupported.")
    namespace, identifier, *tail = parts
    if namespace == "runtime-batches" and identifier in runtime_ids:
        if parts[-1].endswith((".py", ".sh", ".pending", ".lock")) or parts[-1] in {
            "requirements.txt",
            ".in-use",
        }:
            return None
        if len(tail) == 1 and (tail[0] in CONTROL or re.fullmatch(ID + r"-inputs\.json", tail[0])):
            return "processing_evidence"
        if (
            len(tail) == 3
            and tail[0] == "discard-evidence"
            and re.fullmatch(ID, tail[1])
            and tail[2] in CONTROL
        ):
            return "processing_evidence"
    if (
        namespace == "preservation"
        and identifier in restore_ids
        and len(tail) == 1
        and tail[0]
        in {
            "source.sqlite3",
            "manifest.json",
            "COMMITTED.json",
            "restore-receipt.json",
        }
    ):
        return "processing_evidence"
    raise ValueError("A library contains evidence unsupported by the portable inventory policy.")


def capture_artifact_requirements(db, candidates, *, guard=None):
    """Validate promises made by trusted source rows; older absent evidence stays explicit.

    This is a capture resolver on the Store's own consistent snapshot, not an incoming
    database parser. Immutable version contexts supply attribution, never filesystem paths.
    """
    import json

    from . import portable_payload, portable_transport
    from .history import validate_import_artifact_evidence

    indexed = {
        (item["recording_id"], item["relative_path"]): (path, base)
        for path, base, item in candidates
    }
    captured_jobs, captured_imports, gaps = set(), set(), set()

    def verify_required(required, recording_id, job_id, folder="enhancements"):
        for expected in required:
            name = expected["filename"]
            relative = name if folder is None else folder + "/" + job_id + "/" + name
            try:
                path, base = indexed[(recording_id, relative)]
            except KeyError:
                raise ValueError("A promised processing artifact is missing.") from None
            count = expected["bytes"]
            if count is None:
                import wave

                count = portable_transport._node(path, base).st_size
                with wave.open(str(path), "rb") as audio:
                    if (
                        audio.getframerate(),
                        audio.getnchannels(),
                        audio.getsampwidth(),
                        audio.getnframes(),
                    ) != (16000, 1, 2, expected["audio_frames"]):
                        raise ValueError("A promised processing input has changed audio frames.")
            portable_transport._read(
                path,
                base,
                maximum=count,
                size=count,
                expected_hash=expected["sha256"],
                guard=guard,
            )

    total = 0
    for recording_id, job_id, result_id, context in db.execute(
        "SELECT r.recording_id,r.job_id,r.id,v.context "
        "FROM result_versions v "
        "JOIN transcript_results r ON r.id=v.result_id ORDER BY v.id"
    ):
        portable_transport._guard(guard)
        raw = context.encode("utf-8")
        total += len(raw)
        if total > 64 * 1024**2:
            raise ValueError("The portable artifact metadata exceeds the supported capture limit.")
        portable_payload._preflight(raw, guard)
        value = json.loads(
            raw,
            object_pairs_hook=portable_payload._pairs,
            parse_float=portable_payload._float,
            parse_constant=portable_payload._constant,
        )
        if type(value) is not dict:
            raise ValueError("A captured version context is not a JSON object.")
        if "artifact_evidence" in value and value["artifact_evidence"] != "not_captured":
            receipt = validate_import_artifact_evidence(value["artifact_evidence"])
            relative = "sources/" + receipt["source_id"] + "." + receipt["format"]
            try:
                path, base = indexed[(recording_id, relative)]
            except KeyError:
                raise ValueError("A promised transcript import artifact is missing.") from None
            portable_transport._read(
                path,
                base,
                maximum=receipt["bytes"],
                size=receipt["bytes"],
                expected_hash=receipt["sha256"],
                guard=guard,
            )
            captured_imports.add(recording_id)
        if "producer_artifacts" in value:
            # The accepted T43 leaf validates exact native receipts and returns only fixed
            # filenames. No receipt-controlled path is followed.
            from . import local_asr_capture

            if job_id is None:
                raise ValueError("Captured producer artifacts have no producing job owner.")
            origin = db.execute(
                "SELECT id,payload_sha256,context FROM result_versions "
                "WHERE result_id=? AND sequence=1",
                (result_id,),
            ).fetchone()
            if origin is None:
                raise ValueError("Captured producer artifacts have no immutable creation.")
            portable_payload._preflight(origin[2].encode("utf-8"), guard)
            origin_context = json.loads(origin[2], object_pairs_hook=portable_payload._pairs)
            if origin_context.get("producer_artifacts") != value["producer_artifacts"]:
                raise ValueError("Inherited producer artifacts changed from their creation.")
            required = local_asr_capture.inventory(
                value["producer_artifacts"],
                recording_id=recording_id,
                job_id=job_id,
                creation={
                    "result_id": result_id,
                    "version_id": origin[0],
                    "payload_sha256": origin[1],
                },
            )
            verify_required(required, recording_id, job_id)
            captured_jobs.add(job_id)
        if "native_artifacts" in value:
            # Fixed recording-root leaves named by the owning job; no receipt paths.
            from . import native_capture

            if job_id is None:
                raise ValueError("Captured native artifacts have no producing job owner.")
            origin = db.execute(
                "SELECT id,payload_sha256,context FROM result_versions "
                "WHERE result_id=? AND sequence=1",
                (result_id,),
            ).fetchone()
            if origin is None:
                raise ValueError("Captured native artifacts have no immutable creation.")
            portable_payload._preflight(origin[2].encode("utf-8"), guard)
            origin_context = json.loads(origin[2], object_pairs_hook=portable_payload._pairs)
            if origin_context.get("native_artifacts") != value["native_artifacts"]:
                raise ValueError("Inherited native artifacts changed from their creation.")
            required = native_capture.inventory(
                value["native_artifacts"],
                recording_id=recording_id,
                job_id=job_id,
                creation={
                    "result_id": result_id,
                    "version_id": origin[0],
                    "payload_sha256": origin[1],
                },
            )
            verify_required(required, recording_id, job_id, None)
            captured_jobs.add(job_id)
        if "provider_artifacts" in value:
            # Fixed request/output leaves beneath chunks/<owning job>; no receipt paths.
            from . import provider_capture

            if job_id is None:
                raise ValueError("Captured provider artifacts have no producing job owner.")
            origin = db.execute(
                "SELECT id,payload_sha256,context FROM result_versions "
                "WHERE result_id=? AND sequence=1",
                (result_id,),
            ).fetchone()
            if origin is None:
                raise ValueError("Captured provider artifacts have no immutable creation.")
            portable_payload._preflight(origin[2].encode("utf-8"), guard)
            origin_context = json.loads(origin[2], object_pairs_hook=portable_payload._pairs)
            if origin_context.get("provider_artifacts") != value["provider_artifacts"]:
                raise ValueError("Inherited provider artifacts changed from their creation.")
            required = provider_capture.inventory(
                value["provider_artifacts"],
                recording_id=recording_id,
                job_id=job_id,
                creation={
                    "result_id": result_id,
                    "version_id": origin[0],
                    "payload_sha256": origin[1],
                },
            )
            verify_required(required, recording_id, job_id, "chunks")
            captured_jobs.add(job_id)
        if "timing_artifacts" in value:
            from . import timing_capture

            evidence = value["timing_artifacts"]
            if type(evidence) is not dict or type(evidence.get("creation")) is not dict:
                raise ValueError("The captured timing artifact references are invalid.")
            applied = db.execute(
                "SELECT id,payload_sha256,context FROM result_versions WHERE id=? AND result_id=?",
                (evidence["creation"].get("version_id"), result_id),
            ).fetchone()
            parent = db.execute(
                "SELECT id,recording_id,kind,params,chunks FROM jobs WHERE id=? AND recording_id=?",
                (evidence.get("application_job_id"), recording_id),
            ).fetchone()
            if applied is None or parent is None:
                raise ValueError("The captured timing artifact owner is missing.")
            portable_payload._preflight(applied[2].encode("utf-8"), guard)
            if (
                json.loads(applied[2], object_pairs_hook=portable_payload._pairs).get(
                    "timing_artifacts"
                )
                != evidence
            ):
                raise ValueError("Inherited timing artifacts changed from their application.")
            job = dict(zip(("id", "recording_id", "kind", "params", "chunks"), parent, strict=True))
            required = timing_capture.inventory(
                evidence,
                job,
                creation={
                    "result_id": result_id,
                    "version_id": applied[0],
                    "payload_sha256": applied[1],
                },
            )
            verify_required(required, recording_id, parent[0])
            captured_jobs.add(parent[0])
            if any(unit == "not_captured" for unit in evidence["units"].values()):
                gaps.add((recording_id, parent[0], "timing_units_not_captured"))
    for identifier, recording_id, params in db.execute(
        "SELECT id,recording_id,params FROM jobs WHERE kind IN ('transcribe','enhance') ORDER BY id"
    ):
        portable_transport._guard(guard)
        try:
            raw = params.encode("utf-8")
            portable_payload._preflight(raw, None)
            document = json.loads(
                raw,
                object_pairs_hook=portable_payload._pairs,
                parse_float=portable_payload._float,
                parse_constant=portable_payload._constant,
            )
        except (UnicodeError, ValueError, TypeError):
            document = None
        if type(document) is dict and "producer_artifact_capture" in document:
            from . import local_asr_capture

            required = local_asr_capture.inventory(
                document["producer_artifact_capture"],
                recording_id=recording_id,
                job_id=identifier,
                creation=None,
            )
            verify_required(required, recording_id, identifier)
            captured_jobs.add(identifier)
        if type(document) is dict and "native_artifact_capture" in document:
            from . import native_capture

            capture = document["native_artifact_capture"]
            required = native_capture.inventory(
                capture, recording_id=recording_id, job_id=identifier, creation=None
            )
            verify_required(required, recording_id, identifier, None)
            captured_jobs.add(identifier)
            if capture["output"] == "not_captured":
                gaps.add((recording_id, identifier, "native_outputs_not_captured"))
        if type(document) is dict and "provider_artifact_capture" in document:
            from . import provider_capture

            capture = document["provider_artifact_capture"]
            required = provider_capture.inventory(
                capture, recording_id=recording_id, job_id=identifier, creation=None
            )
            verify_required(required, recording_id, identifier, "chunks")
            captured_jobs.add(identifier)
            if any(unit["cached_part_sha256"] for unit in capture["units"].values()):
                gaps.add((recording_id, identifier, "provider_outputs_not_captured"))
        if type(document) is dict and "timing_artifact_capture" in document:
            from . import timing_capture

            job = {
                "id": identifier,
                "recording_id": recording_id,
                "kind": "enhance",
                "params": document,
            }
            required = timing_capture.inventory(document["timing_artifact_capture"], job)
            verify_required(required, recording_id, identifier)
            captured_jobs.add(identifier)
            if any(
                unit == "not_captured"
                for unit in document["timing_artifact_capture"]["units"].values()
            ):
                gaps.add((recording_id, identifier, "timing_units_not_captured"))
        if identifier not in captured_jobs:
            gaps.add((recording_id, identifier, "producer_artifacts_not_captured"))
    # Strict Runtime proof/attempt/file validation is performed by the mandatory contained
    # semantic pass before publication. Here only the exact snapshot's deletion/liveness
    # facts decide whether the narrowly permitted unavailable-artifact gap must be declared.
    for job_id, recording_id, inputs, remote, live_recording, live_job in db.execute(
        "SELECT m.job_id,m.recording_id,a.input_receipt,a.remote_receipt,"
        "EXISTS(SELECT 1 FROM recordings r WHERE r.id=m.recording_id),"
        "EXISTS(SELECT 1 FROM jobs j WHERE j.id=m.job_id) "
        "FROM runtime_batch_members m JOIN runtime_batch_attempts a ON a.job_id=m.job_id "
        "ORDER BY m.job_id,a.attempt"
    ):
        portable_transport._guard(guard)
        observed = []
        for encoded in (inputs, remote):
            try:
                raw = encoded.encode("utf-8")
                portable_payload._preflight(raw, None)
                value = json.loads(
                    raw,
                    object_pairs_hook=portable_payload._pairs,
                    parse_float=portable_payload._float,
                    parse_constant=portable_payload._constant,
                )
            except (ValueError, TypeError, UnicodeError, AttributeError):
                value = None
            observed.append(value)
        present = (type(observed[0]) is dict and "artifact_origin" in observed[0]) or (
            type(observed[1]) is dict and "artifact_checkpoints" in observed[1]
        )
        if present:
            if not live_recording and not live_job:
                gaps.add((recording_id, job_id, "absent_owner_artifacts_not_retained"))
            elif live_recording:
                gaps.discard((recording_id, job_id, "producer_artifacts_not_captured"))
                gaps.add((recording_id, job_id, "runtime_worker_environment_not_captured"))
    for _path, _base, item in candidates:
        if item["role"] == "import_source" and item["recording_id"] not in captured_imports:
            gaps.add((item["recording_id"], None, "import_attribution_not_captured"))
    return [
        {"recording_id": recording, "job_id": job, "code": code}
        for recording, job, code in sorted(gaps, key=lambda gap: (gap[0], gap[1] or "", gap[2]))
    ]
