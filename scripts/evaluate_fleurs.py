"""Score saved predictions offline, or explicitly evaluate a running local app."""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from cbs_transcriber.evaluation import load_suite, read_predictions, score
from cbs_transcriber.history import encode_payload
from cbs_transcriber.providers import Settings, preserve_response

SUITE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fleurs_da"
PROVIDER_FIELDS = ("base_url", "model", "language", "capability", "max_upload_mb")


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ValueError("Evaluation received an invalid saved identity.")
    return value


def accepted_jobs(jobs, recordings, settings):
    """Bind the accepted frozen configuration, not a pre-POST default-settings observation."""
    if not isinstance(jobs, list) or len(jobs) != len(recordings):
        raise ValueError("Evaluation requires exactly one accepted job per recording.")
    try:
        expected = Settings.model_validate(settings).model_dump()
    except (TypeError, ValueError):
        raise ValueError("Evaluation received invalid provider settings.") from None
    selected, seen = {}, set()
    for job in jobs:
        try:
            job_id, recording_id = identifier(job["id"]), identifier(job["recording_id"])
            params = job["params"]
            if job["kind"] == "transcribe":
                frozen = params["settings"]
                source_model = frozen["model"]
                origin = {key: frozen[key] for key in ("model", "base_url", "language")}
            elif (
                job["kind"] == "enhance"
                and params.get("transcription") is True
                and not params.get("retranscription")
                and not params.get("realign")
                and not any(
                    key in params
                    for key in ("source_snapshot", "segment_id", "segment_ids", "timing_units")
                )
            ):
                connection = params.get("ucloud_api")
                if "input_intent" in params:
                    from cbs_transcriber import input_admission

                    intent = input_admission._validate(params["input_intent"])
                    accepted = intent["accepted"]
                    if (
                        intent["phase"] == "terminal"
                        or intent["cancel_requested"]
                        or intent["predecessor"] is not None
                        or accepted["operation"] != "transcription"
                        or job["kind"] != input_admission._kind(accepted)
                        or input_admission._canonical(params)
                        != input_admission._canonical(input_admission._envelope(intent))
                    ):
                        raise ValueError
                    connection = {
                        "provider_id": accepted["provider"]["id"],
                        "settings": accepted["provider"]["settings"],
                    }
                frozen = connection["settings"]
                options = params["enhancement"]
                if (
                    options.get("transcribe") is not True
                    or options.get("retranscribe") is not False
                    or options.get("execution") != "ucloud"
                    or expected["provider_type"] != "ucloud"
                    or connection["provider_id"] != settings["id"]
                ):
                    raise ValueError
                source_model = options["whisper_model"]
                origin = {
                    "model": source_model,
                    "language": options["language"],
                    "ucloud_server": frozen["base_url"],
                    "ucloud_provider_id": connection["provider_id"],
                }
            else:
                raise ValueError
            if (
                job_id in seen
                or recording_id in selected
                or recording_id not in recordings.values()
                or params["provider_id"] != settings["id"]
                or not isinstance(frozen, dict)
                or not isinstance(source_model, str)
                or not source_model
                or Settings.model_validate(frozen).model_dump() != expected
            ):
                raise ValueError
            encoded = json.dumps(frozen, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ValueError(
                "Accepted evaluation jobs differ from the selected provider or recordings. "
                "Jobs remain in the app; inspect them before starting a new run."
            ) from None
        selected[recording_id] = {
            "id": job_id,
            "recording_id": recording_id,
            "kind": job["kind"],
            "provider_id": settings["id"],
            "source_model": source_model,
            "source_fields": {"provider_id": settings["id"], **origin},
            "settings_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        seen.add(job_id)
    return selected


def creation_version(client, job, completed):
    """Read immutable machine output even after review edits or active-result switches."""
    if not isinstance(completed, dict) or (
        completed.get("recording_id") != job["recording_id"]
        or completed.get("kind") != job["kind"]
        or completed.get("id") != job["id"]
    ):
        raise ValueError("The completed evaluation job has inconsistent ownership.")
    recording_id, result_id = job["recording_id"], identifier(completed.get("result_id"))
    base = f"/api/recordings/{recording_id}/transcripts/{result_id}"
    result = request(client, "GET", base).json()
    if not isinstance(result, dict) or (
        result.get("id") != result_id
        or result.get("recording_id") != recording_id
        or result.get("job_id") != job["id"]
        or not isinstance(result.get("source"), dict)
        or result["source"].get("kind") != "provider"
        or any(result["source"].get(key) != value for key, value in job["source_fields"].items())
    ):
        raise ValueError("The saved evaluation result does not belong to its producer.")
    versions = request(
        client, "GET", base + "/versions", params={"before_sequence": 2, "limit": 1}
    ).json()
    if not isinstance(versions, list) or len(versions) != 1:
        raise ValueError("Evaluation requires an immutable machine creation version.")
    metadata = versions[0]
    if not isinstance(metadata, dict):
        raise ValueError("Evaluation received invalid creation metadata.")
    version_id = identifier(metadata.get("id"))
    version_path = base + "/versions/" + version_id
    version = request(client, "GET", version_path).json()
    for row in (metadata, version):
        if not isinstance(row, dict) or (
            not {"predecessor_id", "source_version_id"}.issubset(row)
            or row.get("result_id") != result_id
            or type(row.get("sequence")) is not int
            or row["sequence"] != 1
            or row.get("predecessor_id") is not None
            or row.get("source_version_id") is not None
            or row.get("actor_kind") != "worker"
            or row.get("action") != "provider_result"
        ):
            raise ValueError("Evaluation requires an unchanged machine creation receipt.")
    # The metadata list deliberately omits private context; the exact version retains it.
    context = version.get("context")
    if (
        not isinstance(context, dict)
        or context.get("producer_job_id") != job["id"]
        or context.get("processing_kind") != job["kind"]
    ):
        raise ValueError("The evaluation creation version has an inconsistent producer.")
    payload = version.get("payload", {})
    try:
        _, digest = encode_payload(payload["segments"], payload["speakers"])
        if (
            payload.get("format") != "cbs-result-snapshot-v1"
            or version.get("id") != version_id
            # The scoped version API proves recording ownership without exposing this column.
            or version.get("recording_id", recording_id) != recording_id
            or version.get("payload_sha256") != digest
            or metadata.get("payload_sha256") != digest
            or any(not isinstance(item.get("text"), str) for item in payload["segments"])
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("The evaluation creation payload failed its integrity check.") from None
    return (
        payload,
        version_path,
        {
            "job_id": job["id"],
            "recording_id": recording_id,
            "result_id": result_id,
            "version_id": version_id,
            "payload_sha256": digest,
            "settings_sha256": job["settings_sha256"],
            "producer_model": job["source_model"],
            "export_format": "text",
        },
    )


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        # Avoid dumping response bodies or configuration supplied by external providers.
        raise ValueError(f"App request failed: {method} {path} (HTTP {response.status_code}).")
    return response


def wait_for_jobs(client, identifiers, timeout, poll_interval):
    deadline = time.monotonic() + timeout
    while True:
        rows = request(client, "GET", "/api/jobs").json()
        if not isinstance(rows, list):
            raise ValueError("Evaluation received invalid job status.")
        jobs = {}
        for job in rows:
            if not isinstance(job, dict):
                raise ValueError("Evaluation received invalid job status.")
            if job.get("id") in identifiers:
                if job["id"] in jobs:
                    raise ValueError("Evaluation received ambiguous job status.")
                if job.get("state") not in {
                    "queued",
                    "running",
                    "completed",
                    "failed",
                    "cancelled",
                    "interrupted",
                }:
                    raise ValueError("Evaluation received invalid job status.")
                jobs[job["id"]] = job
        if set(jobs) != set(identifiers):
            raise ValueError("Evaluation job status is unavailable. Inspect the retained run.")
        states = [jobs[identifier]["state"] for identifier in identifiers]
        if any(state in {"failed", "cancelled", "interrupted"} for state in states):
            raise ValueError(
                "Evaluation job stopped. Inspect its status in the app; retry explicitly."
            )
        if all(state == "completed" for state in states):
            return jobs
        if time.monotonic() >= deadline:
            raise ValueError(
                "Evaluation timed out. Jobs remain in the app; inspect before retrying."
            )
        time.sleep(poll_interval)


def run_app_suite(client, suite, manifest, output, *, timeout=1800, poll_interval=0.5):
    """Use the app's normal upload, conversion, queue, transcript and export APIs."""
    output.mkdir(parents=True, exist_ok=False)
    settings = request(client, "GET", "/api/settings").json()
    # Only allowlisted non-secret fields enter reports.
    provider = {key: settings[key] for key in ("id", *PROVIDER_FIELDS)}
    identifier(provider["id"])
    timestamp = datetime.now(UTC).isoformat()
    project = request(
        client, "POST", "/api/projects", json={"name": f"FLEURS Danish evaluation {timestamp}"}
    ).json()
    run = {
        "started_at": timestamp,
        "project_id": project["id"],
        "provider": provider,
        "dataset_revision": manifest["revision"],
        "recordings": {},
        "media_jobs": [],
        "results": {},
        "status": "running",
    }

    def checkpoint():
        preserve_response(output / "run.json", run)

    checkpoint()
    started = time.monotonic()
    try:
        for clip in manifest["clips"]:
            with (suite / clip["file"]).open("rb") as audio:
                uploaded = request(
                    client,
                    "POST",
                    "/api/recordings",
                    data={"project_id": project["id"]},
                    files={"file": (clip["file"], audio, "audio/wav")},
                ).json()
            run["recordings"][clip["id"]] = uploaded["recording"]["id"]
            run["media_jobs"].append(uploaded["job"]["id"])
            checkpoint()
        wait_for_jobs(client, run["media_jobs"], timeout, poll_interval)
        current_settings = request(client, "GET", "/api/settings").json()
        if any(current_settings[key] != value for key, value in provider.items()):
            raise ValueError("Provider settings changed during evaluation; start a new run.")
        jobs = request(
            client,
            "POST",
            "/api/transcriptions",
            json={
                "recording_ids": list(run["recordings"].values()),
                "provider_ids": [provider["id"]],
            },
        ).json()
        run["transcription_jobs"] = (
            [job["id"] for job in jobs if isinstance(job, dict) and isinstance(job.get("id"), str)]
            if isinstance(jobs, list)
            else []
        )
        checkpoint()
        accepted = accepted_jobs(jobs, run["recordings"], settings)
        completed = wait_for_jobs(client, run["transcription_jobs"], timeout, poll_interval)
        predictions = {}
        for clip_id, recording_id in run["recordings"].items():
            job = accepted[recording_id]
            payload, version_path, receipt = creation_version(client, job, completed[job["id"]])
            run["results"][clip_id] = receipt
            checkpoint()
            predictions[clip_id] = " ".join(s["text"] for s in payload["segments"])
            receipt["prediction_text_sha256"] = hashlib.sha256(
                predictions[clip_id].encode("utf-8")
            ).hexdigest()
            exported = request(client, "GET", version_path + "/export", params={"format": "text"})
            (output / f"{clip_id}.nvivo.txt").write_bytes(exported.content)
            receipt["export_sha256"] = hashlib.sha256(exported.content).hexdigest()
            receipt["export_bytes"] = len(exported.content)
            checkpoint()
        (output / "predictions.json").write_text(
            json.dumps(predictions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        report = score(manifest, predictions)
        run.update(status="completed", elapsed_seconds=time.monotonic() - started)
        return {**report, "run": run}
    except BaseException:
        run["status"] = "incomplete"
        raise
    finally:
        checkpoint()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=SUITE)
    commands = parser.add_subparsers(dest="command", required=True)
    offline = commands.add_parser(
        "score", help="Score clip-ID to text JSON without network requests"
    )
    offline.add_argument("predictions", type=Path)
    offline.add_argument("--output", type=Path, required=True, help="New report JSON path")
    live = commands.add_parser(
        "run", help="Create an evaluation project using configured app settings"
    )
    live.add_argument("--app-url", default="http://127.0.0.1:8765")
    live.add_argument(
        "--allow-api-calls", action="store_true", help="Accept provider use and charges"
    )
    live.add_argument("--output", type=Path, required=True, help="New directory for run files")
    live.add_argument("--timeout", type=float, default=1800, help="Seconds per queue stage")
    args = parser.parse_args(argv)
    if args.command == "run":
        if not args.allow_api_calls:
            parser.error("run requires --allow-api-calls; the configured provider may charge.")
        url = urlsplit(args.app_url)
        if (
            url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username
            or url.password
            or url.path not in {"", "/"}
            or url.query
            or url.fragment
        ):
            parser.error("--app-url must be a loopback HTTP app URL without credentials or paths.")
        if args.timeout <= 0:
            parser.error("--timeout must be positive.")
    try:
        manifest = load_suite(args.suite)
        if args.command == "score":
            predictions = read_predictions(args.predictions)
            report = score(manifest, predictions)
            with args.output.open("x", encoding="utf-8") as destination:
                json.dump(report, destination, ensure_ascii=False, indent=2)
        else:
            with httpx.Client(
                base_url=args.app_url, headers={"X-CBS-Request": "1"}, timeout=60, trust_env=False
            ) as client:
                report = run_app_suite(
                    client, args.suite, manifest, args.output, timeout=args.timeout
                )
            (args.output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
    except (ValueError, OSError, KeyError, httpx.HTTPError) as exc:
        # Deliberately omit exception bodies which may contain private endpoint details.
        detail = str(exc) if isinstance(exc, ValueError) else ""
        print(f"Evaluation failed ({type(exc).__name__}). {detail}")
        return 1
    print(f"{report['coverage']['scored']} clips: WER {report['wer']:.2%}, CER {report['cer']:.2%}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
