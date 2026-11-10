"""Build an inert native B200/MIG qualification plan. No live execution entrypoint.

Supplied app/product metadata is unverified. Only committed public FLEURS inputs are read.
The new disposable Store contains cancelled snapshots at an offline.invalid endpoint.
"""

import argparse
import hashlib
import io
import json
import math
import os
import re
import wave
from copy import deepcopy
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path, PurePosixPath
from types import SimpleNamespace


def require_packaged_catalog():
    if "CBS_TRANSCRIBER_MODELS" in os.environ:
        raise ValueError("Qualification requires the packaged native catalog, without an override.")


# Providers loads presets eagerly. Refuse overrides before any application package import.
require_packaged_catalog()

from cbs_transcriber import ucloud_native as native  # noqa: E402
from cbs_transcriber.providers import Settings  # noqa: E402
from cbs_transcriber.runtime_compute import MIG_PRODUCT  # noqa: E402
from cbs_transcriber.store import Store  # noqa: E402
from cbs_transcriber.transcripts import MAX_TRANSCRIPT_BYTES, parse_transcript  # noqa: E402

SCHEMA = "native-transcriber-mig-offline-v1"
MAX_JSON_BYTES = 65536
SUITE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "fleurs_da"
FIXTURES = {
    "da_dk_0000": "05e5e021501884b9e98fd0b74b0f040e3c1283b1079fb77c0c6745e12e1bfed6",
    "da_dk_0001": "266d8f430b8b379629abd87a1c4e78f5017cf2f86bacaa3c8197349786ce8611",
}
SOURCE_REVISION = "1cca811bb8ea4d370345f108f00518167040282c"
FIXTURE_SOURCE = {
    "dataset": "FluidInference/fleurs-full (Danish Google FLEURS test subset)",
    "revision": SOURCE_REVISION,
    "source": "https://huggingface.co/datasets/FluidInference/fleurs-full",
    "license": "CC-BY-4.0",
    "license_url": "https://creativecommons.org/licenses/by/4.0/",
    "attribution": (
        "FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech. "
        "Alexis Conneau, Min Ma, Simran Khanuja, Yu Zhang, Vera Axelrod, Siddharth Dalmia, "
        "Jason Riber, Clara Talber, Ankur Bapna (2022). WAV extraction/naming: FluidInference."
    ),
    "changes": "Two unchanged public clips selected and copied to ID-owned recording folders.",
}
SCOPES = {
    "metadata_eligibility": "not_checked",
    "live_execution": "not_checked",
    "gpu_execution": "not_checked",
    "local_result_commit": "not_checked",
    "cleanup_authority": "not_granted",
    "public_admission": "not_checked",
}


def encoded(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def read_json(path):
    def nonfinite(_):
        raise ValueError("Nonfinite JSON values are not supported.")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key in supplied metadata.")
            result[key] = value
        return result

    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Supplied metadata exceeds the 64 KiB limit.")
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("Supply bounded UTF-8 application/product JSON.") from None


def text(value, *, blank=False):
    if (
        not isinstance(value, str)
        or len(value) > 200
        or (not blank and not value.strip())
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid application metadata text.")
    return value


def application_snapshot(value):
    """Freeze a bounded allowlist, excluding supplied funding/support/credential claims."""
    if not isinstance(value, dict) or len(encoded(value)) > MAX_JSON_BYTES:
        raise ValueError("Supply a bounded application object.")
    try:
        metadata, invocation = value["metadata"], value["invocation"]
        reference = {k: text(metadata[k]) for k in ("name", "version", "flavorName")}
        if reference["flavorName"] != "Batch" or "transcrib" not in reference["name"].lower():
            raise ValueError("Use the native Transcriber Batch application.")
        parameters = invocation["parameters"]
        if not isinstance(parameters, list) or not 1 <= len(parameters) <= 50:
            raise ValueError("Supply at most 50 declared application parameters.")
        projected = []
        kinds = {
            "input_file",
            "input_directory",
            "text",
            "textarea",
            "enumeration",
            "integer",
            "floating_point",
            "boolean",
        }
        for parameter in parameters:
            item = {k: text(parameter[k]) for k in ("name", "type")}
            if item["type"] not in kinds:
                raise ValueError("Unsupported application parameter type.")
            if "optional" in parameter:
                if type(parameter["optional"]) is not bool:
                    raise ValueError("Parameter optionality must be boolean.")
                item["optional"] = parameter["optional"]
            if item["type"] == "enumeration":
                options = parameter.get("options")
                if not isinstance(options, list) or not 1 <= len(options) <= 100:
                    raise ValueError("Supply at most 100 declared enumeration options.")
                item["options"] = [
                    {
                        k: text(option[k], blank=k == "value")
                        for k in ("name", "value")
                        if k in option
                    }
                    for option in options
                ]
                if any("value" not in option for option in item["options"]):
                    raise ValueError("Enumeration options require values.")
            default = parameter.get("defaultValue")
            if isinstance(default, dict):
                default = default.get("value")
            if default is not None and item["type"] not in {"input_file", "input_directory"}:
                expected_types = {
                    "boolean": {bool},
                    "integer": {int},
                    "floating_point": {int, float},
                    "text": {str},
                    "textarea": {str},
                    "enumeration": {str},
                }
                if type(default) not in expected_types[item["type"]]:
                    raise ValueError("The declared default does not match its parameter type.")
                if isinstance(default, str):
                    text(default, blank=True)
                    if default and item["name"] not in {
                        "model",
                        "language",
                        "output_format",
                        "outputFormat",
                    }:
                        raise ValueError(
                            "Unowned nonempty text defaults are excluded from this plan."
                        )
                elif type(default) not in {bool, int, float} or (
                    type(default) is float and not math.isfinite(default)
                ):
                    raise ValueError("Invalid application parameter default.")
                item["defaultValue"] = default
            projected.append(item)
        result = {
            "metadata": reference,
            "invocation": {
                "applicationType": text(invocation["applicationType"]),
                "parameters": projected,
            },
        }
        native.batch_overrides(result, native.application_parameters(result))
        return result
    except (KeyError, TypeError, AttributeError):
        raise ValueError("Malformed native application metadata.") from None


def products(full, mig):
    full = native.NativeSetup(product=full).product
    mig = native.NativeSetup(product=mig).product
    if not full or full["id"] != native.COMPUTE_ID or full["provider"] != MIG_PRODUCT["provider"]:
        raise ValueError("Supply the exact full-B200 control product reference.")
    if mig != MIG_PRODUCT:
        raise ValueError("Supply the exact B200 MIG candidate product reference.")
    return {"b200": full, "mig": mig}


def fixture_inputs():
    manifest = read_json(SUITE / "manifest.json")
    if manifest.get("revision") != SOURCE_REVISION or manifest.get("license") != "CC-BY-4.0":
        raise ValueError("The committed public fixture source identity changed.")
    clips = {item["id"]: item for item in manifest["clips"]}
    facts, bodies = [], {}
    for identifier, expected in FIXTURES.items():
        clip = clips[identifier]
        path = SUITE / (identifier + ".wav")
        if clip["file"] != path.name or clip["sha256"] != expected or path.is_symlink():
            raise ValueError("The exact public fixture identity changed.")
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("The exact public fixture bytes changed.")
        with wave.open(io.BytesIO(raw), "rb") as audio:
            geometry = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
            frames = audio.getnframes()
            if geometry != (1, 2, 16000) or len(audio.readframes(frames)) != frames * 2:
                raise ValueError("The public fixture WAV geometry changed.")
        facts.append({"id": identifier, "sha256": expected, "bytes": len(raw), "frames": frames})
        bodies[identifier] = raw
    return facts, bodies


def canonical_model(model):
    model = text(model)
    require_packaged_catalog()
    packaged = read_json(files("cbs_transcriber").joinpath("catalogs/provider_models.json"))
    if model not in {entry["model"] for entry in packaged["ucloud_transcriber"]["models"]}:
        raise ValueError(
            "Choose an exact canonical native catalog model ID, not an alias or Runtime ID."
        )
    if native.provider_catalog() != packaged:
        raise ValueError(
            "Qualification requires the packaged native catalog, without cached overrides."
        )
    return model


def settings_for(application, product, model):
    model = canonical_model(model)
    parameters = native.application_parameters(application)
    names = {}
    for field, aliases, kind in (
        ("input_parameter", ("input_file", "inputFile"), "input_file"),
        ("output_parameter", ("output_dir", "outputDir"), "input_directory"),
        ("format_parameter", ("output_format", "outputFormat"), "enumeration"),
    ):
        matches = [name for name in aliases if parameters.get(name, {}).get("type") == kind]
        if len(matches) != 1:
            raise ValueError("Require unambiguous known native input/output/JSON parameter names.")
        names[field] = matches[0]
    return Settings(
        provider_type="ucloud",
        base_url="https://offline.invalid",
        model=model,
        language="da",
        capability="words_speakers",
        ucloud_transcriber={
            "project_id": "offline-native-mig",
            "storage_path": "/offline-native-mig/qualification",
            "application": {k: application["metadata"][k] for k in ("name", "version")},
            "product": product,
            "hours": 1,
            "cleanup_after_success": False,
            **names,
        },
    )


def specification(application, settings, batch_id):
    canonical_model(settings.model)

    class Snapshot:
        def request(self, method, path, *, params):
            if (method, path, params) != (
                "GET",
                "/api/hpc/apps/byNameAndVersion",
                {
                    "appName": application["metadata"]["name"],
                    "appVersion": application["metadata"]["version"],
                },
            ):
                raise ValueError("The offline snapshot cannot perform UCloud requests.")
            return SimpleNamespace(json=lambda: deepcopy(application))

    template, parameters = native.template_info(Snapshot(), settings)
    folder = settings.ucloud_transcriber.storage_path + "/cbs-batch-" + batch_id
    result, _ = native.build_directory_specification(
        settings, template, parameters, folder, batch_id
    )
    if result["product"] != settings.ucloud_transcriber.product:
        raise ValueError("The native builder changed the frozen product.")
    return result, native.capture_selector(settings, result)


def create_plan(directory, application, full_product, mig_product, *, model="base"):
    application = application_snapshot(application)
    chosen = products(full_product, mig_product)
    facts, bodies = fixture_inputs()
    if version("ucloud-workflow") != "0.1.9":
        raise ValueError("This adapter requires the released ucloud-workflow 0.1.9 builder.")
    frozen = {lane: settings_for(application, product, model) for lane, product in chosen.items()}
    for settings in frozen.values():
        specification(
            application, settings, "0" * 32
        )  # Refuse invalid choices before creating a Store.
    directory = Path(directory).absolute()
    directory.mkdir()  # Exclusive: never open, migrate or overwrite an existing library/output.
    (directory / "OFFLINE_ONLY.txt").write_text(
        "Disposable qualification plan. No worker or live execution. "
        "Do not use as a serving library.\n",
        encoding="utf-8",
    )
    store = Store(directory / "library")
    runs = {}
    with store.library_lock():
        project = store.create_project("Offline native B200/MIG qualification")
        for lane, settings in frozen.items():
            items, fixtures = [], {}
            for fixture in facts:
                recording = store.create_recording(project["id"], fixture["id"] + ".wav")
                folder = store.directory(recording["id"])
                folder.mkdir(parents=True)
                original = folder / "original.wav"
                original.write_bytes(bodies[fixture["id"]])
                if original.read_bytes() != bodies[fixture["id"]]:
                    raise ValueError("The copied public original differs from its frozen fixture.")
                store.execute(
                    "UPDATE recordings SET status='ready',duration=? WHERE id=?",
                    (fixture["frames"] / 16000, recording["id"]),
                )
                fixtures[recording["id"]] = fixture["id"]
                items.append(
                    {
                        "recording_id": recording["id"],
                        "params": {
                            "settings": settings.model_dump(),
                            "provider_id": "offline-" + lane,
                            "provider_name": "Offline qualification " + lane,
                            "revision": recording["revision"],
                            "qualification_plan_only": True,
                        },
                    }
                )
            jobs = store.add_transcription_jobs(items)
            batch_id = jobs[0]["params"]["native_batch_id"]
            store.finish_native_batch(
                batch_id, "cancelled", "Offline qualification plan; no execution"
            )
            batch = store.native_batch(batch_id)
            spec, selector = specification(application, settings, batch_id)
            runs[lane] = {
                "batch_id": batch_id,
                "settings": settings.model_dump(),
                "members": [
                    {**member, "fixture_id": fixtures[member["recording_id"]]}
                    for member in batch["members"]
                ],
                "specification": spec,
                "selector": selector,
            }
    plan = {
        "schema": SCHEMA,
        "scopes": dict(SCOPES),
        "workflow_version": "0.1.9",
        "application": application,
        "model": model,
        "fixture_revision": SOURCE_REVISION,
        "fixture_source": dict(FIXTURE_SOURCE),
        "fixtures": facts,
        "local_project_id": project["id"],
        "local_job_state": "cancelled",
        "runs": runs,
    }
    plan["sha256"] = digest(plan)
    validate_plan(plan)
    with (directory / "plan.json").open("xb") as stream:
        stream.write(encoded(plan) + b"\n")
    return plan


def validate_plan(plan):
    try:
        return _validate_plan(plan)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed offline qualification plan.") from None


def _validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {
        "schema",
        "scopes",
        "workflow_version",
        "application",
        "model",
        "fixture_revision",
        "fixture_source",
        "fixtures",
        "local_project_id",
        "local_job_state",
        "runs",
        "sha256",
    }:
        raise ValueError("Invalid offline qualification plan shape.")
    unsigned = {k: v for k, v in plan.items() if k != "sha256"}
    if (
        plan["sha256"] != digest(unsigned)
        or plan["schema"] != SCHEMA
        or plan["scopes"] != SCOPES
        or plan["workflow_version"] != "0.1.9"
        or plan["local_job_state"] != "cancelled"
        or plan["fixture_revision"] != SOURCE_REVISION
        or plan["fixture_source"] != FIXTURE_SOURCE
        or plan["fixtures"] != fixture_inputs()[0]
        or application_snapshot(plan["application"]) != plan["application"]
        or set(plan["runs"]) != {"b200", "mig"}
    ):
        raise ValueError("The frozen offline qualification plan changed.")
    chosen = products(
        *(
            plan["runs"][lane]["settings"]["ucloud_transcriber"]["product"]
            for lane in ("b200", "mig")
        )
    )
    identifiers = [plan["local_project_id"]]
    for lane, run in plan["runs"].items():
        if set(run) != {"batch_id", "settings", "members", "specification", "selector"}:
            raise ValueError("Invalid qualification run shape.")
        settings = settings_for(plan["application"], chosen[lane], plan["model"])
        spec, selector = specification(plan["application"], settings, run["batch_id"])
        if (
            run["settings"] != settings.model_dump()
            or run["specification"] != spec
            or run["selector"] != selector
        ):
            raise ValueError("The frozen native specification/settings identity changed.")
        if len(run["members"]) != 2:
            raise ValueError("Each qualification allocation requires exactly two fixture members.")
        identifiers.append(run["batch_id"])
        for member, fixture in zip(run["members"], plan["fixtures"], strict=True):
            if (
                set(member) != {"job_id", "recording_id", "filename", "fixture_id"}
                or member["fixture_id"] != fixture["id"]
                or member["filename"] != member["job_id"] + ".wav"
            ):
                raise ValueError("The frozen fixture/member mapping changed.")
            identifiers += [member["job_id"], member["recording_id"]]
    if len(set(identifiers)) != len(identifiers) or any(
        not isinstance(x, str) or not re.fullmatch("[0-9a-f]{32}", x) for x in identifiers
    ):
        raise ValueError("Qualification identities must be unique generated UUIDs.")
    return plan


def _run(plan, lane):
    if lane not in {"b200", "mig"}:
        raise ValueError("Choose the exact b200 or mig qualification owner.")
    return validate_plan(plan)["runs"][lane]


def validate_specification(plan, lane, supplied):
    run = _run(plan, lane)
    if supplied != run["specification"]:
        raise ValueError("Supplied specification does not match this exact qualification owner.")
    return {"specification_identity": "matches", **SCOPES}


def validate_results(plan, lane, supplied):
    run = _run(plan, lane)
    if not isinstance(supplied, list) or len(supplied) != 2:
        raise ValueError("Supply exactly two per-file output observations.")
    members = {member["job_id"]: member for member in run["members"]}
    receipts, seen = [], set()
    for item in supplied:
        if not isinstance(item, dict) or set(item) != {
            "job_id",
            "recording_id",
            "input_filename",
            "output_name",
            "data",
        }:
            raise ValueError("Invalid per-file observation shape.")
        if any(
            not isinstance(item[key], str)
            for key in ("job_id", "recording_id", "input_filename", "output_name")
        ):
            raise ValueError("Per-file identities must be literal strings.")
        member = members.get(item["job_id"])
        if (
            not member
            or item["job_id"] in seen
            or (item["recording_id"], item["input_filename"])
            != (member["recording_id"], member["filename"])
        ):
            raise ValueError("The output is not bound to this exact fixture member.")
        seen.add(item["job_id"])
        stem, selector = PurePosixPath(member["filename"]).stem, run["selector"]
        names = {stem + ".json", member["filename"] + ".json"}
        if selector["model"] == "parakeet":
            names.add(stem + "_parakeet.json")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", selector["model"]):
            names.add(f"{stem}_{selector['model']}_{selector['language']}.json")
        data = item["data"]
        if (
            item["output_name"] not in names
            or not isinstance(data, bytes)
            or not 0 < len(data) <= MAX_TRANSCRIPT_BYTES
        ):
            raise ValueError("The output name or bounded transcript bytes do not match the member.")
        segments = parse_transcript(data, "transcript.json")
        if not any(segment["text"].strip() for segment in segments):
            raise ValueError("A speech qualification output must contain transcript text.")
        receipts.append(
            {
                "job_id": member["job_id"],
                "recording_id": member["recording_id"],
                "fixture_id": member["fixture_id"],
                "output_name": item["output_name"],
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "plan_sha256": plan["sha256"],
        "batch_id": run["batch_id"],
        "outputs": receipts,
        **SCOPES,
    }


def validate_cleanup_identity(plan, lane, claim):
    run = _run(plan, lane)
    folder = run["settings"]["ucloud_transcriber"]["storage_path"] + "/cbs-batch-" + run["batch_id"]
    expected = {
        "batch_id": run["batch_id"],
        "folder": folder,
        "job_ids": sorted(member["job_id"] for member in run["members"]),
    }
    if claim != expected:
        raise ValueError(
            "Cleanup identity must name only this exact allocation and its two members."
        )
    return {"cleanup_identity": "matches", **SCOPES}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application-json", type=Path, required=True)
    parser.add_argument("--b200-product-json", type=Path, required=True)
    parser.add_argument("--mig-product-json", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, required=True, help="A new disposable local directory"
    )
    parser.add_argument("--model", default="base")
    args = parser.parse_args(argv)
    create_plan(
        args.output,
        read_json(args.application_json),
        read_json(args.b200_product_json),
        read_json(args.mig_product_json),
        model=args.model,
    )
    print(json.dumps({"plan": str(args.output.absolute() / "plan.json"), **SCOPES}))


if __name__ == "__main__":
    main()
