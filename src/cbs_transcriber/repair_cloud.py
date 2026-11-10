"""One API-only MIG allocation for a frozen selected-clip alignment bundle."""

import copy
import json
import threading
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import httpx
from ucloud_workflow import UCloudAPIError

from . import enhancement_api as api
from . import providers, repair_bundles, repair_remote, runtime_compute
from . import ucloud_native as native
from .enhancement import EnhancementOptions
from .enhancement_worker import MODELS
from .media import Cancelled
from .store import now
from .transcripts import normalize_segments

PROTOCOL = repair_remote.PROTOCOL
HERE = Path(__file__).parent
FROZEN_FIELDS = (
    "enhancement",
    "language_preference",
    "source_snapshot",
    "segment_id",
    "segment_ids",
    "timing_units",
    "realign",
    "passage_timing",
    "audio_input",
    "audio_preparations",
    "repair_runtime",
    "repair_cloud",
    "ucloud_api",
    "repair_routing",
    "repair_source",
    "routing",
)


def identity(params):
    return repair_remote.digest(
        repair_remote.encoded({key: params[key] for key in FROZEN_FIELDS if key in params})
    )


def application_fingerprint(job):
    repair = job["params"].get("ucloud_run", {}).get("repair", {})
    return repair_remote.digest(
        repair_remote.encoded(
            {
                "protocol": PROTOCOL,
                "job_id": job["id"],
                "recording_id": job["recording_id"],
                "frozen": identity(job["params"]),
                "bundle_sha256": repair.get("bundle_sha256"),
                "published_units": repair.get("published_units"),
                "output": job["params"].get("realign_output"),
                "remote_id": job["params"].get("ucloud_run", {}).get("job_id"),
            }
        )
    )


def code_files():
    files = {path.name: path for path in api.WORKER_FILES}
    files["enhancement_remote.py"] = HERE / "repair_remote.py"
    if (
        not {"enhancement_worker.py", "enhancement_remote.py"}
        <= set(files)
        <= repair_remote.CODE_NAMES
    ):
        raise ValueError("The selected-clip controller has an unsupported source inventory.")
    return files


def code_bytes(path):
    data = repair_bundles._read(path, path.parent, repair_remote.MAX_CODE).replace(b"\r\n", b"\n")
    if not 0 < len(data) <= repair_remote.MAX_CODE:
        raise ValueError("The selected-clip runtime source exceeds its byte limit.")
    return data


def require_options(options):
    if (
        options.execution != "ucloud"
        or options.device != "cuda"
        or not options.align
        or options.diarize
        or options.transcribe
        or options.retranscribe
        or options.allow_downloads
        or options.prepare_runtime
        or options.language not in {"da", "en"}
        or options.timing_policy != "retain-estimates"
    ):
        raise ValueError(
            "UCloud passage repair requires prepared, fixed Danish/English alignment only."
        )


def freeze(store, credentials, project_ids, options):
    """Fresh-request read-only preparation/compute checks; never prepare or reroute a job."""
    options = options.model_copy(update={"device": "cuda", "timing_policy": "retain-estimates"})
    require_options(options)
    chosen = api.connection(store, credentials, project_ids)
    settings = providers.Settings.model_validate(chosen["settings"])
    key = credentials.get(providers.credential_scope(settings))
    with native.client_for(settings, key) as client:
        if not api.stamped(client, settings):
            raise ValueError("Prepare the UCloud runtime before requesting passage repair.")
    compute = runtime_compute.freeze(settings, credentials)
    codes = {name: repair_remote.receipt(code_bytes(path)) for name, path in code_files().items()}
    repo, revision = MODELS[options.language]
    runtime = {
        "runtime_id": api.runtime_id(),
        "worker_sha256": codes["enhancement_worker.py"]["sha256"],
        "language": options.language,
        "alignment_model": {"repo": repo, "revision": revision},
    }
    return options, {
        "ucloud_api": {
            **chosen,
            "runtime_id": api.runtime_id(),
            "prepare_runtime": False,
            **compute,
        },
        "repair_runtime": runtime,
        "repair_cloud": {"protocol": PROTOCOL, "bundle_generation": 1, "code": codes},
    }


def require_plan(job, options):
    require_options(options)
    plan = job["params"].get("repair_cloud")
    execution = job["params"].get("ucloud_api", {})
    if (
        job.get("kind") != "enhance"
        or "input_intent" in job["params"]
        or not isinstance(plan, dict)
        or set(plan) != {"protocol", "bundle_generation", "code"}
        or plan["protocol"] != PROTOCOL
        or type(plan["bundle_generation"]) is not int
        or plan["bundle_generation"] < 1
        or not isinstance(plan["code"], dict)
        or not {"enhancement_worker.py", "enhancement_remote.py"}
        <= set(plan["code"])
        <= repair_remote.CODE_NAMES
        or execution.get("batch_plan") is not None
        or job["params"].get("runtime_batch_id") is not None
        or execution.get("prepare_runtime") is not False
        or execution.get("runtime_compute_policy") != runtime_compute.POLICY
        or runtime_compute.frozen_product(execution) != runtime_compute.MIG_PRODUCT
    ):
        raise ValueError("This job does not carry a valid singleton selected-clip repair plan.")
    for expected in plan["code"].values():
        if (
            not isinstance(expected, dict)
            or set(expected) != {"sha256", "bytes"}
            or not repair_bundles._hash(expected["sha256"])
            or type(expected["bytes"]) is not int
            or not 0 < expected["bytes"] <= repair_remote.MAX_CODE
        ):
            raise ValueError("The frozen selected-clip code identity is invalid.")
    runtime = job["params"].get("repair_runtime", {})
    if (
        runtime.get("runtime_id") != execution.get("runtime_id")
        or runtime.get("worker_sha256") != plan["code"]["enhancement_worker.py"]["sha256"]
        or runtime.get("language") != options.language
    ):
        raise ValueError("The frozen repair code, runtime and language identities disagree.")
    return plan


def folder_for(store, job):
    return repair_bundles._filesystem_path(
        store.directory(job["recording_id"]) / "enhancements" / job["id"]
    )


def save_bytes(path, data, root):
    """Never overwrite a valid or invalid saved receipt; identical bytes are reusable."""
    if path.exists():
        if repair_bundles._read(path, root, max(len(data), 1)) != data:
            raise ValueError("A saved selected-clip receipt changed; preserve this generation.")
        return
    repair_bundles._write(path, data, root)


def for_output(bundle):
    manifest = copy.deepcopy(bundle["manifest"])
    for index, unit in enumerate(manifest["units"]):
        path = bundle["directory"] / "units" / f"{index:05d}" / "request.json"
        unit["_request"] = repair_remote.decoded(
            repair_bundles._read(path, bundle["directory"], repair_remote.MAX_REQUEST)
        )
    return manifest


def project_outputs(bundle, outputs):
    """Timing-only replacements; the app remains the sole version/projection writer."""
    manifest = for_output(bundle)
    result = {}
    for index, output in outputs.items():
        repair_remote.validate_output(output, manifest, index)
        mapped = repair_bundles.map_alignment_output(bundle, output)
        unit = manifest["units"][index]
        for passage in unit["passages"]:
            words = [
                {key: word[key] for key in ("word", "start", "end")}
                for word in mapped["words"]
                if passage["char_start"]
                <= word["char_start"]
                < word["char_end"]
                <= passage["char_end"]
            ]
            if not any(word["start"] is not None and word["end"] is not None for word in words):
                continue
            text = unit["_request"]["text"][passage["char_start"] : passage["char_end"]]
            row = normalize_segments(
                [{"text": text, "start": passage["start"], "end": passage["end"], "words": words}]
            )[0]
            timing = {"words": row["words"], "word_timing": row["word_timing"]}
            if (passage["start"] is None or passage["end"] is None) and row["word_timing"]:
                timing.update(start=row["words"][0]["start"], end=row["words"][-1]["end"])
            result[passage["id"]] = timing
    return result


def local_output(folder, manifest, index):
    unit = folder / "repair-results" / f"{index:05d}"

    def read(name):
        return repair_remote.decoded(
            repair_bundles._read(unit / name, folder, repair_remote.MAX_REQUEST)
        )

    ack = read("accepted.json")
    if ack != repair_remote.accepted(manifest, index) or read("started.json") != {
        "format": repair_remote.STARTED_FORMAT,
        "accepted": repair_remote.receipt(repair_remote.encoded(ack)),
    }:
        repair_remote.failure()
    output = repair_remote.validate_output(read("output.json"), manifest, index)
    proof = read("local-receipt.json")
    expected = repair_remote.success_receipt(manifest, index, output)
    if (
        not isinstance(proof, dict)
        or set(proof) != {"receipt", "origin"}
        or proof["receipt"] != expected
        or proof["origin"] not in {"remote", "terminal_recovery", "pending_recovery"}
    ):
        repair_remote.failure()
    return output


def require_cleanup_evidence(store, job, bundle):
    journal = job["params"].get("ucloud_run", {})
    repair = journal.get("repair", {})
    units = repair.get("units")
    if (
        journal.get("remote_state") not in api.TERMINAL
        or repair.get("protocol") != PROTOCOL
        or repair.get("bundle_sha256") != bundle["sha256"]
        or repair.get("terminal_harvest") is not True
        or not isinstance(units, dict)
        or set(units) != {str(index) for index in range(len(bundle["manifest"]["units"]))}
    ):
        raise ValueError("Recover this repair's accepted clip outputs before remote cleanup.")
    folder, manifest = folder_for(store, job), for_output(bundle)
    for key, value in units.items():
        index = int(key)
        if not isinstance(value, dict) or set(value) != {"state", "evidence"}:
            repair_remote.failure()
        if value["state"] == "completed":
            local_output(folder, manifest, index)
        elif value["state"] == "unstarted":
            if value["evidence"] != {}:
                repair_remote.failure()
        elif value["state"] == "failed":
            evidence = value["evidence"]
            if not isinstance(evidence, dict) or set(evidence) != {
                "accepted.json",
                "started.json",
                "failure.json",
            }:
                repair_remote.failure()
            observed = {}
            for name, expected in evidence.items():
                if (
                    not isinstance(expected, dict)
                    or set(expected) != {"sha256", "bytes"}
                    or not repair_bundles._hash(expected["sha256"])
                ):
                    repair_remote.failure()
                path = folder / "repair-artifacts" / f"{index:05d}" / (expected["sha256"] + ".json")
                raw = repair_bundles._read(path, folder, repair_remote.MAX_REQUEST)
                if repair_remote.receipt(raw) != expected:
                    repair_remote.failure()
                observed[name] = repair_remote.decoded(raw)
            ack = repair_remote.accepted(manifest, index)
            if (
                observed["accepted.json"] != ack
                or observed["started.json"]
                != {
                    "format": repair_remote.STARTED_FORMAT,
                    "accepted": repair_remote.receipt(repair_remote.encoded(ack)),
                }
                or observed["failure.json"]
                != {
                    "format": repair_remote.FAILURE_FORMAT,
                    "accepted": repair_remote.receipt(repair_remote.encoded(ack)),
                    "code": "processing",
                }
            ):
                repair_remote.failure()
        else:
            raise ValueError("Recover this repair's accepted clip outputs before remote cleanup.")


def validate_local_outputs(store, job):
    """Admission proof for applying recovered timing from a terminal failed/cancelled job."""
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    plan = require_plan(job, options)
    bundle = repair_bundles.validate_bundle(store, job["id"], generation=plan["bundle_generation"])
    journal = job["params"].get("ucloud_run", {})
    repair = journal.get("repair", {})
    selected = repair.get("published_units")
    if (
        journal.get("remote_state") not in api.TERMINAL
        or repair.get("protocol") != PROTOCOL
        or repair.get("bundle_sha256") != bundle["sha256"]
        or repair.get("published") is not True
        or not isinstance(selected, list)
        or any(
            type(index) is not int or not 0 <= index < len(bundle["manifest"]["units"])
            for index in selected
        )
        or len(selected) != len(set(selected))
    ):
        raise ValueError("The terminal repair has no verified, published local timing receipt.")
    manifest = for_output(bundle)
    outputs = {index: local_output(folder_for(store, job), manifest, index) for index in selected}
    projected = project_outputs(bundle, outputs)
    unresolved = [
        p["id"] for unit in manifest["units"] for p in unit["passages"] if p["id"] not in projected
    ]
    if (
        not projected
        or job["params"].get("realign_output") != {"passages": projected}
        or type(repair.get("unresolved_count")) is not int
        or repair["unresolved_count"] != len(unresolved)
        or repair.get("unresolved_ids") != unresolved
    ):
        raise ValueError("The saved repair timing no longer matches its verified local receipts.")
    return {
        "passages": projected,
        "published_units": selected,
        "unresolved_count": repair["unresolved_count"],
        "fingerprint": application_fingerprint(job),
    }


def merge_journal(
    store, job, known_run, values, *, frozen_identity, publication=None, message=None, states=None
):
    """One repair writer for worker, Stop and cleanup observations; no runtime or files needed."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT recording_id,kind,params,state FROM jobs WHERE id=?", (job["id"],)
        ).fetchone()
        if row is None or row["kind"] != "enhance" or row["recording_id"] != job["recording_id"]:
            repair_remote.failure()
        if states is not None and row["state"] not in states:
            raise ValueError("The repair state changed. Reload its Activity before continuing.")
        latest = json.loads(row["params"])
        if "input_intent" in latest or identity(latest) != frozen_identity:
            raise ValueError("The frozen repair owner changed; its journal was retained.")
        journal = latest.setdefault("ucloud_run", {})
        updates = copy.deepcopy(values)
        for key in ("job_id", "owned_folder", "script_application"):
            known = known_run.get(key)
            if (
                known is not None
                and journal.get(key) != known
                or known is None
                and journal.get(key) is not None
                and updates.get(key) != journal[key]
            ):
                raise ValueError("The repair's observed remote owner changed.")
            if journal.get(key) is not None and key in updates and updates[key] != journal[key]:
                raise ValueError("A repair's remote ownership receipt cannot be replaced.")
        if journal.get("remote_state") in api.TERMINAL:
            updates.pop("remote_state", None)
        if journal.get("termination_requested"):
            updates.pop("termination_requested", None)
        if journal.get("cleanup_state") in native.CLEANED:
            updates.pop("cleanup_state", None)
        if journal.get("capacity_released_at"):
            updates.pop("capacity_reserved", None)
        if publication is not None and journal.get("repair", {}).get("published"):
            if latest.get("realign_output", {}) != publication:
                raise ValueError("This repair already published a different timing receipt.")
            updates.pop("repair", None)
        journal.update(updates)
        if publication:
            latest["realign_output"] = copy.deepcopy(publication)
        db.execute(
            "UPDATE jobs SET params=?,message=COALESCE(?,message),updated=? WHERE id=?",
            (json.dumps(latest), message, now(), job["id"]),
        )
    return store.job(job["id"])


class Run(api.Run):
    """Reuse the established singleton allocation/control plane; change only clip execution."""

    def __init__(self, worker, job, recording, bundle, options):
        self.plan = require_plan(job, options)
        self.frozen_identity = identity(job["params"])
        self.bundle, self.manifest = bundle, for_output(bundle)
        super().__init__(
            worker, job, recording, folder_for(worker.store, job), options, lambda: None
        )
        if bundle["manifest"]["runtime"] != job["params"]["repair_runtime"]:
            repair_remote.failure()
        if bundle["manifest"]["options"].get("timing_policy") != options.timing_policy:
            repair_remote.failure()
        self.repair = copy.deepcopy(
            self.run.get(
                "repair", {"protocol": PROTOCOL, "bundle_sha256": bundle["sha256"], "units": {}}
            )
        )
        if (
            self.repair.get("protocol") != PROTOCOL
            or self.repair.get("bundle_sha256") != bundle["sha256"]
        ):
            repair_remote.failure()

    def merge_journal(self, values, *, publication=None, message=None):
        from . import restored_owners

        restored_owners.require_job(self.store, self.job["id"])
        fresh = merge_journal(
            self.store,
            self.job,
            self.run,
            values,
            frozen_identity=self.frozen_identity,
            publication=publication,
            message=message,
        )
        current_run = self.run
        current_run.clear()
        current_run.update(fresh["params"].get("ucloud_run", {}))
        fresh["params"]["ucloud_run"] = current_run
        self.job.update(fresh)
        self.run = current_run

    def checkpoint(self, **values):
        self.merge_journal(values)

    def guard(self):
        super().guard()
        observed = self.store.one(
            "SELECT kind,recording_id,json_extract(params,'$.ucloud_run.job_id') AS remote_id,"
            "json_extract(params,'$.ucloud_run.termination_requested') AS stopping,"
            "json_extract(params,'$.ucloud_run.remote_state') AS remote_state FROM jobs WHERE id=?",
            (self.identifier,),
        )
        if (
            observed is None
            or observed["kind"] != "enhance"
            or observed["recording_id"] != self.recording["id"]
            or observed["remote_id"] != self.run.get("job_id")
        ):
            raise ValueError("The repair's private-transfer owner changed.")
        if observed["stopping"]:
            raise Cancelled()
        if observed["remote_state"] in api.TERMINAL:
            raise ValueError("The repair allocation no longer accepts private inputs.")

    def checkpoint_repair(self):
        self.checkpoint(repair=copy.deepcopy(self.repair))

    def cache_code(self):
        directory = self.folder / "repair-code"
        directory.mkdir(exist_ok=True)
        sources = code_files()
        for name, expected in self.plan["code"].items():
            path = directory / name
            data = (
                repair_bundles._read(path, self.folder, repair_remote.MAX_CODE)
                if path.exists()
                else code_bytes(sources[name])
            )
            if repair_remote.receipt(data) != expected:
                raise ValueError("The frozen repair runtime source changed. Start a new repair.")
            save_bytes(path, data, self.folder)

    def upload(self, path, name):
        if name in repair_remote.CODE_NAMES:
            path = self.folder / "repair-code" / name
            if (
                name not in self.plan["code"]
                or repair_remote.receipt(
                    repair_bundles._read(path, self.folder, repair_remote.MAX_CODE)
                )
                != self.plan["code"][name]
            ):
                repair_remote.failure()
        return super().upload(path, name)

    def submit(self):
        self.cache_code()
        return super().submit()

    def send_inputs(self):
        doctor = api.read_json(self.client, self.server, self.remote + "/doctor.json")
        cache = api.read_json(self.client, self.server, self.remote + "/cache.json")
        if doctor is None or cache is None:
            return False
        if (
            doctor.get("version") != api.WHISPERX_VERSION
            or not doctor.get("ready")
            or not (doctor.get("gpu") or {}).get("available")
        ):
            raise api.RuntimeNotReady(
                "The UCloud repair runtime has no ready CUDA device. No clips were uploaded."
            )
        runtime_compute.require_gpu_memory(doctor)
        if not (cache.get("models") or {}).get(self.options.language) or not cache.get("punkt_tab"):
            raise api.RuntimeNotReady(
                "The selected alignment model is not prepared. No clips were uploaded."
            )
        repair_bundles.validate_bundle(
            self.store, self.identifier, generation=self.plan["bundle_generation"]
        )
        self.guard()
        current = self.store.job(self.identifier)["params"].get("ucloud_run", {})
        if current.get("termination_requested") or current.get("remote_state") in api.TERMINAL:
            raise ValueError("This repair allocation no longer accepts private inputs.")
        # A surviving checksum trigger proves the immutable upload already finished.
        names = (
            ["repair-run.json"]
            + ["bundle/" + name for name in ("manifest.json", "COMMITTED.json")]
            + [
                f"bundle/units/{index:05d}/{name}"
                for index in range(len(self.manifest["units"]))
                for name in ("audio.wav", "request.json")
            ]
        )
        control = {
            "protocol": PROTOCOL,
            "generation": self.identifier,
            "bundle_sha256": self.bundle["sha256"],
            "code": self.plan["code"],
        }
        save_bytes(self.folder / "repair-run.json", repair_remote.encoded(control), self.folder)
        payloads = {
            "repair-run.json": self.folder / "repair-run.json",
            **{
                name: self.bundle["directory"] / name.removeprefix("bundle/")
                for name in names
                if name.startswith("bundle/")
            },
        }
        limit = max(repair_remote.MAX_AUDIO, repair_remote.MAX_JSON)
        checksum = "".join(
            f"{repair_remote.digest(repair_bundles._read(path, self.folder, limit))}  {name}\n"
            for name, path in payloads.items()
        ).encode()
        old_trigger = api.read_remote(
            self.client, self.server, self.remote + "/manifest.sha256", api.MAX_STATUS_BYTES
        )
        if old_trigger is not None:
            if old_trigger != checksum:
                repair_remote.failure()
            return True
        self.checkpoint(cleanup_state="pending")
        for name, path in payloads.items():
            parent = PurePosixPath(name).parent
            for ancestor in reversed((parent, *parent.parents)):
                if str(ancestor) != ".":
                    api.ensure_folder(self.client, self.remote + "/" + str(ancestor))
            self.upload(path, name)
        save_bytes(self.folder / "repair-upload.sha256", checksum, self.folder)
        self.upload(self.folder / "repair-upload.sha256", "manifest.sha256")
        return True

    def artifact(self, index, name):
        raw = api.read_remote(
            self.client,
            self.server,
            self.remote + f"/out/{index:05d}/" + name,
            repair_remote.MAX_REQUEST,
        )
        if raw is None:
            return None
        archive = self.folder / "repair-artifacts" / f"{index:05d}"
        archive.mkdir(parents=True, exist_ok=True)
        save_bytes(archive / (repair_remote.digest(raw) + ".json"), raw, self.folder)
        return raw

    def harvest(self):
        if self.run.get("remote_state") not in api.TERMINAL:
            raise api.Transient("The repair allocation has not reached a verified terminal state.")
        for index in range(len(self.manifest["units"])):
            cached = self.repair["units"].get(str(index))
            if cached and cached["state"] == "completed":
                local_output(self.folder, self.manifest, index)
                continue
            raw = {
                name: self.artifact(index, name)
                for name in (
                    "accepted.json",
                    "started.json",
                    "output.json",
                    "receipt.json",
                    "failure.json",
                )
            }
            source_name = "output.json"
            if raw[source_name] is None:
                source_name = "output.json.pending"
                raw[source_name] = self.artifact(index, source_name)
            state = "unstarted" if all(value is None for value in raw.values()) else "uncertain"
            try:
                ack = repair_remote.decoded(raw["accepted.json"], repair_remote.MAX_STATUS)
                if ack != repair_remote.accepted(self.manifest, index):
                    repair_remote.failure()
                started = repair_remote.decoded(raw["started.json"], repair_remote.MAX_STATUS)
                if started != {
                    "format": repair_remote.STARTED_FORMAT,
                    "accepted": repair_remote.receipt(raw["accepted.json"]),
                }:
                    repair_remote.failure()
                if raw.get(source_name) is not None:
                    output = repair_remote.validate_output(
                        repair_remote.decoded(raw[source_name], repair_remote.MAX_REQUEST),
                        self.manifest,
                        index,
                    )
                    expected = repair_remote.success_receipt(self.manifest, index, output)
                    origin = "remote" if raw["receipt.json"] is not None else "terminal_recovery"
                    if source_name == "output.json.pending":
                        origin = "pending_recovery"
                    if (
                        raw["receipt.json"] is not None
                        and repair_remote.decoded(raw["receipt.json"], repair_remote.MAX_STATUS)
                        != expected
                    ):
                        repair_remote.failure()
                    local = self.folder / "repair-results" / f"{index:05d}"
                    local.mkdir(parents=True, exist_ok=True)
                    for name in ("accepted.json", "started.json"):
                        save_bytes(local / name, raw[name], self.folder)
                    save_bytes(local / "output.json", raw[source_name], self.folder)
                    save_bytes(
                        local / "local-receipt.json",
                        repair_remote.encoded({"receipt": expected, "origin": origin}),
                        self.folder,
                    )
                    state = "completed"
                elif raw["failure.json"] is not None and repair_remote.decoded(
                    raw["failure.json"], repair_remote.MAX_STATUS
                ) == {
                    "format": repair_remote.FAILURE_FORMAT,
                    "accepted": repair_remote.receipt(raw["accepted.json"]),
                    "code": "processing",
                }:
                    state = "failed"
            except (ValueError, TypeError, KeyError, UnicodeError):
                pass  # Invalid or missing proof is retained, never treated as a completed clip.
            self.repair["units"][str(index)] = {
                "state": state,
                "evidence": {
                    name: repair_remote.receipt(data)
                    for name, data in raw.items()
                    if data is not None
                },
            }
            self.checkpoint_repair()
        self.repair["terminal_harvest"] = True
        self.checkpoint_repair()

    def publish(self):
        if self.repair.get("published"):
            return
        if (
            not self.repair.get("terminal_harvest")
            or self.run.get("remote_state") not in api.TERMINAL
        ):
            repair_remote.failure()
        indices = [
            int(index)
            for index, value in self.repair["units"].items()
            if value["state"] == "completed"
        ]
        outputs = {index: local_output(self.folder, self.manifest, index) for index in indices}
        projected = project_outputs(self.bundle, outputs)
        counts = {
            state: sum(value["state"] == state for value in self.repair["units"].values())
            for state in ("completed", "failed", "uncertain", "unstarted")
        }
        unresolved = [
            passage["id"]
            for unit in self.manifest["units"]
            for passage in unit["passages"]
            if passage["id"] not in projected
        ]
        self.repair.update(
            published=True,
            published_units=indices,
            unresolved_count=len(unresolved),
            unresolved_ids=unresolved,
            counts=counts,
        )
        self.merge_journal(
            {"repair": self.repair},
            publication={"passages": projected} if projected else {},
            message=(
                f"UCloud repair: {counts['completed']} clips complete; "
                f"{len(unresolved)} passages remain unresolved."
            ),
        )

    def finish_terminal(self):
        self.harvest()
        self.publish()
        if self.settings.ucloud_transcriber.cleanup_after_success and not any(
            value["state"] == "uncertain" for value in self.repair["units"].values()
        ):
            require_cleanup_evidence(self.store, self.store.job(self.identifier), self.bundle)
            api.cleanup(self.client, self.run, self.remote, self.checkpoint)

    def collect(self):
        api.stop(self.client, self.run, self.checkpoint)
        from .runtime_capacity import drain

        fresh = drain(
            self.store,
            self.worker.credentials,
            self.job,
            client=self.client,
            checkpoint=self.checkpoint,
            wait=self.worker.cancel_event.wait,
        )
        if fresh["params"]["ucloud_run"].get("remote_state") not in api.TERMINAL:
            raise api.Transient("The repair is stopping; retry to recover its clip outputs.")
        self.finish_terminal()

    def settle_failure(self):
        # Stop, then recover durable output before considering Trash. Never discard unknown proof.
        try:
            self.collect()
        except (ValueError, OSError, UCloudAPIError, httpx.HTTPError):
            pass

    def execute(self):
        if self.run.get("job_id"):
            key = self.worker.credentials.get(providers.credential_scope(self.settings))
            native.require_setup(self.settings, key)
            with native.client_for(self.settings, key) as self.client:
                try:
                    if (
                        self.run.get("termination_requested")
                        or self.run.get("remote_state") in api.TERMINAL
                    ):
                        self.collect()
                    else:
                        if (
                            not self.run.get("uploaded")
                            and self.params.get("runtime_id") != api.runtime_id()
                        ):
                            raise api.RuntimeNotReady(
                                "This earlier repair runtime cannot receive new private input. "
                                "Start a new repair."
                            )
                        self.poll()
                        self.collect()
                except Cancelled:
                    if not self.worker.stopping.is_set():
                        self.settle_failure()
                    raise
                except api.Transient:
                    raise
                except Exception:
                    self.settle_failure()
                    raise
            return
        return super().execute()


def run(worker, job, recording, *, generation=1):
    """Execute/reconnect; return honest per-unit outcome counts without writing transcript text."""
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    plan = require_plan(job, options)
    if generation != plan["bundle_generation"]:
        raise ValueError("Choose the repair job's frozen bundle generation.")
    bundle = repair_bundles.validate_bundle(worker.store, job["id"], generation=generation)
    execution = Run(worker, job, recording, bundle, options)
    execution.execute()
    fresh = worker.store.job(job["id"])
    journal = fresh["params"].get("ucloud_run", {}).get("repair", {})
    report = {
        "counts": journal.get("counts", {}),
        "unresolved_count": journal.get("unresolved_count"),
        "realign_ready": "realign_output" in fresh["params"],
        "unresolved_ids": journal.get("unresolved_ids", []),
    }
    if any(report["counts"].get(state, 0) for state in ("failed", "uncertain", "unstarted")):
        raise ValueError(
            "Partial repair timing was saved. Start a new repair for unresolved clips."
        )
    return report


def prepare_and_run(worker, job, recording):
    """Prepare only frozen bounded clips in the worker, then execute/reconnect one bundle."""
    from . import restored_owners

    restored_owners.require_job(worker.store, job["id"])
    from . import audio_inputs
    from .jobs import preparation_slot

    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    plan = require_plan(job, options)
    generation = plan["bundle_generation"]
    if "repair_source" in job["params"]:
        from . import repair_admission

        job = repair_admission.prepare(worker, job, recording)
    directory = repair_bundles._filesystem_path(worker.store.directory(recording["id"]))
    bundle_directory, _ = repair_bundles._directory(
        worker.store, {"job_id": job["id"], "recording_id": recording["id"]}, generation
    )
    if bundle_directory.exists():
        repair_bundles.validate_bundle(worker.store, job["id"], generation=generation)
        return run(worker, worker.store.job(job["id"]), recording, generation=generation)
    source = repair_bundles.preparation_plan(worker.store, job["id"])
    folders = []

    def guard():
        if worker.cancel_event.is_set():
            raise Cancelled()

    for index, unit in enumerate(source["units"]):
        guard()
        folder = folder_for(worker.store, job) / "repair-clips" / f"{index:05d}"
        worker.store.update_job(
            job["id"],
            message=f"Preparing selected repair clip {index + 1} of {len(source['units'])}…",
            progress=0.15 * index / len(source["units"]),
        )
        with preparation_slot(getattr(worker, "runtime_preparation_gate", None), guard):
            evidence = audio_inputs.prepare_wav(
                directory,
                recording,
                source["snapshot"],
                folder,
                offset=unit["start_frame"] / repair_bundles.RATE,
                duration=(unit["end_frame"] - unit["start_frame"]) / repair_bundles.RATE,
                cancel=worker.cancel_event,
                **(
                    {"bounded_timeline": True}
                    if source["snapshot"]["duration_basis"] == "container_observation"
                    else {}
                ),
            )
        worker.store.record_audio_preparation(job["id"], evidence)
        folders.append(folder)
    guard()
    fresh = worker.store.job(job["id"])
    repair_bundles.build_bundle(
        worker.store,
        job["id"],
        folders,
        fresh["params"]["repair_runtime"],
        generation=generation,
        cancel=worker.cancel_event,
    )
    return run(worker, worker.store.job(job["id"]), recording, generation=generation)


def stop_owned(store, credentials, job):
    """Fence Stop before bounded control I/O; output recovery remains worker-owned."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    if (
        job.get("kind") != "enhance"
        or job["params"].get("repair_cloud", {}).get("protocol") != PROTOCOL
        or not job["params"].get("ucloud_run", {}).get("job_id")
    ):
        raise ValueError("This repair has no recorded allocation to stop.")
    frozen = identity(job["params"])
    fresh = merge_journal(
        store,
        job,
        job["params"]["ucloud_run"],
        {"termination_requested": True},
        frozen_identity=frozen,
        message="Stopping the owned repair; retained outputs can be recovered.",
    )
    api.terminate_owned(fresh, credentials)


def retry(store, credentials, job):
    """Queue the exact frozen plan; recovery cannot allocate a successor or replay accepted work."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    plan = require_plan(job, options)
    known = job["params"].get("ucloud_run", {})
    directory, _ = repair_bundles._directory(
        store, {"job_id": job["id"], "recording_id": job["recording_id"]}, plan["bundle_generation"]
    )
    if directory.exists():
        repair_bundles.validate_bundle(store, job["id"], generation=plan["bundle_generation"])
    elif known.get("job_id") or known.get("submission_intent") or known.get("submission_attempted"):
        raise ValueError(
            "The submitted repair bundle is missing. Preserve its allocation and evidence."
        )
    elif "audio_input" in job["params"]:
        repair_bundles.preparation_plan(store, job["id"])
    else:
        from . import repair_admission

        repair_admission.validate(job["params"].get("repair_source"))
    _, settings = api.frozen(job)
    record = store.one("SELECT project_id FROM recordings WHERE id=?", (job["recording_id"],))
    if not known.get("uploaded"):
        providers.require_approval(
            store, record["project_id"], job["params"]["ucloud_api"]["provider_id"], settings
        )
    native.require_setup(settings, credentials.get(providers.credential_scope(settings)))
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT state,params FROM jobs WHERE id=?", (job["id"],)).fetchone()
        params = json.loads(row["params"])
        if (
            row["state"] not in {"failed", "cancelled", "interrupted"}
            or identity(params) != identity(job["params"])
            or any(
                params.get("ucloud_run", {}).get(key) != known.get(key)
                for key in (
                    "job_id",
                    "owned_folder",
                    "script_application",
                    "submission_intent",
                    "reconciled_not_created",
                )
            )
            or db.execute(
                "SELECT 1 FROM jobs WHERE recording_id=? AND state IN ('queued','running')",
                (job["recording_id"],),
            ).fetchone()
        ):
            raise ValueError("This repair changed or work is active. Reload before reconnecting.")
        if "repair_source" in params and "audio_input" not in params:
            from . import repair_admission

            source = repair_admission.validate(params["repair_source"])
            params["repair_source"] = repair_admission.validate(
                source | {"epoch": source["epoch"] + 1}
            )
        db.execute(
            "UPDATE jobs SET state='queued',params=?,message=?,updated=? WHERE id=?",
            (json.dumps(params), "Queued to recover the same selected repair", now(), job["id"]),
        )
    return {"ok": True}


def reconcile(store, credentials, job, remote_id, not_created):
    """Record explicit uncertain-allocation resolution through the same fenced journal writer."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    from .remote_jobs import uncertain

    known = job["params"].get("ucloud_run", {})
    if job["params"].get("repair_cloud", {}).get("protocol") != PROTOCOL or not uncertain(known):
        raise ValueError("Only an uncertain selected repair allocation can be reconciled.")
    if bool(remote_id) == bool(not_created):
        raise ValueError("Supply its exact remote job ID or confirm that no job was created.")
    values = {"reconciled_at": now()}
    if not_created:
        values["reconciled_not_created"] = True
    else:
        _, settings = api.frozen(job)
        key = credentials.get(providers.credential_scope(settings))
        with native.client_for(settings, key) as client:
            remote = native.cloud_call(client.retrieve_job, remote_id)
        if remote.get("specification", {}).get("name") != "CBS enhancement " + job["id"]:
            raise ValueError("That remote allocation does not belong to this selected repair.")
        values.update(job_id=remote_id, remote_state=remote.get("status", {}).get("state"))
    merge_journal(
        store,
        job,
        known,
        values,
        frozen_identity=identity(job["params"]),
        states={"failed", "cancelled", "interrupted"},
        message="Repair allocation reconciled.",
    )
    return {"ok": True}


def retry_cleanup(store, credentials, job):
    """Never let generic singleton cleanup discard an unresolved accepted clip."""
    from . import restored_owners

    restored_owners.require_job(store, job["id"])
    options = EnhancementOptions.model_validate(job["params"]["enhancement"])
    plan = require_plan(job, options)
    bundle = repair_bundles.validate_bundle(store, job["id"], generation=plan["bundle_generation"])
    require_cleanup_evidence(store, job, bundle)
    if job["params"].get("realign_output"):
        validate_local_outputs(store, job)
    record = store.one("SELECT id,project_id FROM recordings WHERE id=?", (job["recording_id"],))
    worker = SimpleNamespace(
        store=store,
        credentials=credentials,
        cancel_event=threading.Event(),
        stopping=threading.Event(),
    )
    execution = Run(worker, job, record, bundle, options)
    key = credentials.get(providers.credential_scope(execution.settings))
    native.require_setup(execution.settings, key)
    with native.client_for(execution.settings, key) as client:
        from .runtime_capacity import refresh

        fresh = refresh(store, credentials, store.job(job["id"]), client=client)
        require_cleanup_evidence(store, fresh, bundle)
        if fresh["params"]["ucloud_run"].get("remote_state") not in api.TERMINAL:
            raise ValueError("The repair allocation is still writing; its files were retained.")
        api.cleanup(client, execution.run, execution.remote, execution.checkpoint)
    return execution.run.get("cleanup_state")
