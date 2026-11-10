"""Pinned schema-13–19 observations; incoming SQLite opens only in the disposable child.

This checks bounded research consistency and declared mapping ownership. It does not grant
capture completeness, credential exclusion, remote ownership, quarantine or restore authority.
The schema-12 readers retain their narrower, unchanged contracts.
"""

import base64
import hashlib
import io
import json
import struct
import time
import types
from importlib.resources import files
from pathlib import Path

from . import portable_database as database
from . import portable_transport as transport

PROTOCOL = "cbs-portable-semantics-v1"
POLICY = "cbs-portable-research-consistency-v1"
WORKER_SHA256 = "2931f8eab29f5610e5c83cf77b7ca9868064d46164f67002c92b95723732813c"
PAYLOAD_SHA256 = "7fc37c490bcf1fe0268299f383b9a4951cf1ae351631fd6c5b17ce4cd5465698"
ARTIFACTS_SHA256 = "8bccd9ecb0b4b971af7d20c1b33b349c09ff1729e43f7d99ef7b98e40fe810eb"
CAPTURE_SHA256 = "e383f827430b3af7d19d91561dc675c04b25a87c709573cb9281cd2b4e02d9fe"
INVENTORY_SHA256 = "fe60f40479739e10ef232d59d81bc897e497d30314eeaccc70f574f392516b73"
TRANSCRIPTS_SHA256 = "dcfb95696694c42667d8d1f68618866cb8931a346044e2f572d062297d85845b"
TIMING_SHA256 = "928f6c22a74d5a1287ddab381ef7b1b874a1df69053bb2f5de21d1e05baa0d2c"
RUNTIME_SHA256 = "3ffdee5c209f5ea8188cd2a23a1793334c255f359ded8f96a9ec270f422b16e8"
NATIVE_SHA256 = "480368001624167d89fc8d1577430305e4a4b89b5e5db911ee956885da8d89e6"
PROVIDER_SHA256 = "c67c2469f32db5e2187d43945c02b29a3d1e80940ffaee1141fcaac1b85915e9"
CATALOGS = {
    19: (
        "ae97a3870b69ad21d64ab00e438f2341b031e51f57d2c21b9028af33ca43ed34",
        "76d5d16850e3b262569a600a57a3f2ddbe3315565270d78feee4b5cb97ee63a7",
    ),
    18: (
        "16f38667b49fa43f1912c65e44f77b55bc3b7a9dff5ee056b35e6abd4c716265",
        "d382278a375fd6f72cd14cd8ebfeabf031ba42193a80bca4fe32cf5028b566bc",
    ),
    17: (
        "02ae3a3889767924b0fec5baed8e06298b081c020f34f365a0e28193f303b124",
        "9bc459e4855437b19d7a077cf062ab21d3cdb261b22489d4db324c34919695f7",
    ),
    16: (
        "d406ac0585c53c80c06f0ce527209828ec99217d792c8f7cff0fe9f0b590b1f1",
        "40ea25b0b78cae9d1fad9b071bbeca8d0c5a5d4c88a5ef7a7ae0c935cd54b4bc",
    ),
    13: (
        "b3e3867967d8066ee9ba20f0fea5fb3c68c2ab74e2cf3a0d1c7db2ec246f68a7",
        "2b69fbab37a1fe859ae2befa450b268deecbb22d000dcd8c01aa228bf8db3f27",
    ),
    14: (
        "2d2138ac5d21cc65de2ff2a2700adf33395b10f98db7f5fff515135618e84ea2",
        "11a29b05944ca9db94415b80305b065ad67d35b5595d94aea400c6828d72b2d8",
    ),
    15: (
        "5c16cd5812b57d27dc517dfcaf8f374100a268db94b6d6405cc207f7628850f6",
        "2eab2adde15d71b1c0d48b277fadc79132d879f97c08ce3f8b0bc965f6a510c6",
    ),
}
LIMITS = {
    "worker_bytes": 112 * 1024,
    "request_bytes": 20 * 1024**2,
    "reply_bytes": 4 * 1024**2,
    "seconds": 120,
    "semantic_bytes": 256 * 1024**2,
}
NOT_CHECKED = (
    "sealed_package_binding_and_reference_custody",
    "domain_specific_edit_and_inference_transformations",
    "coding_evidence_semantics",
    "owner_deletion_intent_and_chronology",
    "file_role_resolver_and_capture_completeness",
    "legacy_gap_semantics",
    "credential_exclusion_and_confidential_contents",
    "remote_owner_authority_and_quarantine",
    "restore_and_activation",
    "private_data_security_baseline",
)


def _invalid(part):
    raise ValueError(f"The portable research {part} is invalid or unsupported.")


def _resource(name, pin, maximum, *, source=False):
    with files("cbs_transcriber").joinpath(name).open("rb") as stream:
        # A Windows checkout may use two bytes for every LF in a pinned source.
        # Bound the raw read as well as the normalized content.
        raw = stream.read((maximum * 2 if source else maximum) + 1)
    if source:
        raw = raw.replace(b"\r\n", b"\n")
    if len(raw) > maximum or hashlib.sha256(raw).hexdigest() != pin:
        _invalid("trusted resource identity")
    return raw


def catalog(version):
    """Return the exact approved catalog, never a catalog inferred from an incoming file."""
    if type(version) is not int or version not in CATALOGS:
        _invalid("schema version")
    raw = _resource(f"catalogs/portable_schema_{version}.json", CATALOGS[version][0], 128 * 1024)
    value = json.loads(raw)
    fingerprint = hashlib.sha256(
        b"cbs-portable-schema-v1\0" + database._canonical(value["structure"])
    ).hexdigest()
    if fingerprint != CATALOGS[version][1]:
        _invalid("catalog structure")
    return value["structure"]


def _codec(version):
    """Bind a separate codec instance; never change the schema-12 module's global pins."""
    source = _resource("portable_rows.py", database.CODEC_SHA256_LF, 16 * 1024, source=True)
    raw = _resource(f"catalogs/portable_schema_{version}.json", CATALOGS[version][0], 128 * 1024)
    codec = types.ModuleType("_portable_capture_rows")
    exec(compile(source, "<pinned-portable-capture-rows>", "exec"), codec.__dict__)

    class Resource:
        def joinpath(self, name):
            if name != "catalogs/portable_schema_12.json":
                _invalid("codec resource")
            return self

        def open(self, mode):
            if mode != "rb":
                _invalid("codec resource")
            return io.BytesIO(raw)

    codec.files = lambda name: Resource() if name == "cbs_transcriber" else None
    codec.CATALOG_SHA256, codec.SCHEMA_FINGERPRINT = CATALOGS[version]
    codec._catalog()
    return codec


def database_claims(db, *, guard=None):
    """Hash caller-trusted snapshot rows; this helper never opens a path or accepts a package."""
    version = db.execute("PRAGMA user_version").fetchone()[0]
    structure, codec = catalog(version), _codec(version)
    tables, total_rows, total_bytes = {}, 0, 0
    for name, definition in structure["tables"].items():
        columns = definition["columns"]
        keys = sorted(
            (column for column in columns if column["pk_position"]),
            key=lambda column: column["pk_position"],
        )
        select = ",".join('"' + column["name"] + '"' for column in columns)
        order = ",".join(
            '"'
            + column["name"]
            + '"'
            + (" COLLATE BINARY" if column["declared_type"] == "TEXT" else "")
            for column in keys
        )
        codec.LIMITS["rows"] = min(1_000_000, 2_000_000 - total_rows)
        codec.LIMITS["total_bytes"] = 8 * 1024**3 - total_bytes
        cursor = db.execute(f'SELECT {select} FROM "{name}" ORDER BY {order}')
        try:
            result = codec.digest_table(name, (tuple(row) for row in cursor), guard=guard)
        finally:
            cursor.close()
        total_rows += result["rows"]
        total_bytes += result["encoded_bytes"]
        tables[name] = {key: result[key] for key in ("rows", "sha256")}
    return {
        "schema_version": version,
        "schema_fingerprint": CATALOGS[version][1],
        "tables": tables,
    }


def _bootstrap():
    return (
        "import hashlib,sys\n"
        "n=int.from_bytes(sys.stdin.buffer.read(8),'big')\n"
        f"if not 0<n<={LIMITS['worker_bytes']}:raise SystemExit(2)\n"
        "source=sys.stdin.buffer.read(n)\n"
        f"if len(source)!=n or hashlib.sha256(source).hexdigest()!={WORKER_SHA256!r}:"
        "raise SystemExit(2)\n"
        "exec(compile(source,'<portable-semantics-worker>','exec'),{'__name__':'__main__'})\n"
    )


def _audio_geometry(path, root, frames):
    """Observe a bounded RIFF header; complete object hashing belongs to transport capture."""
    before = transport._node(path, root)
    with path.open("rb") as stream:
        header = stream.read(min(64 * 1024, before.st_size))
    if (
        len(header) < 12
        or header[:4] != b"RIFF"
        or header[8:12] != b"WAVE"
        or struct.unpack_from("<I", header, 4)[0] != before.st_size - 8
    ):
        _invalid("prepared audio geometry")
    position, pcm = 12, False
    while position + 8 <= len(header):
        kind, size = struct.unpack_from("<4sI", header, position)
        start = position + 8
        if kind == b"fmt ":
            if size < 16 or start + 16 > len(header):
                _invalid("prepared audio format")
            pcm = struct.unpack_from("<HHIIHH", header, start) == (1, 1, 16000, 32000, 2, 16)
        elif kind == b"data":
            if not pcm or size != frames * 2 or start + size > before.st_size:
                _invalid("prepared audio frames")
            transport._same_path(before, transport._node(path, root))
            return
        position = start + size + (size & 1)
    _invalid("prepared audio header budget")


def inspect_database(
    path,
    *,
    expected_sha256,
    expected_size,
    manifest,
    guard=None,
    artifact_object_root=None,
    _restore=None,
):
    """Observe one hash-bound file and frozen manifest; caller retains package/capture duties."""
    expected_sha256 = database._sha(expected_sha256)
    expected_size = database._integer(expected_size, database.LIMITS["database_bytes"], 1)
    if guard is not None and not callable(guard):
        _invalid("guard")
    # Freeze only bounded transport metadata; no paths in mappings are opened in this reader.
    raw_manifest = database._canonical(manifest)
    if len(raw_manifest) > transport.LIMITS["manifest_bytes"]:
        _invalid("manifest size")
    frozen = transport._canonical(raw_manifest)
    transport._manifest(frozen, {"backup_id": frozen.get("backup_id")})
    version = frozen["schema_version"]
    structure = catalog(version)
    claims = frozen["database"]
    if (
        claims["object"] != expected_sha256
        or claims["size"] != expected_size
        or claims["schema_fingerprint"] != CATALOGS[version][1]
        or set(claims["tables"]) != set(structure["tables"])
        or any(item["rows"] > 1_000_000 for item in claims["tables"].values())
        or sum(item["rows"] for item in claims["tables"].values()) > 2_000_000
    ):
        _invalid("database binding")
    deadline = time.monotonic_ns() + LIMITS["seconds"] * 10**9

    def check():
        if guard is not None:
            guard()
        if time.monotonic_ns() >= deadline:
            raise TimeoutError("The portable research inspection exceeded its time budget.")

    check()
    path = Path(path)
    if not path.is_absolute():
        _invalid("path")
    parent = path.parent.resolve(strict=True)
    transport._node(path.parent, parent, directory=True)
    transport._node(path, parent)
    path = path.resolve(strict=True)
    object_root = Path(artifact_object_root) if artifact_object_root is not None else parent
    if not object_root.is_absolute():
        _invalid("artifact object root")
    object_root = object_root.resolve(strict=True)
    transport._node(object_root, object_root, directory=True)
    from . import portable_preservation

    preservation_records = portable_preservation.derive(object_root, frozen, check)
    database._sidecars(path)
    _, before = transport._read(
        path,
        parent,
        maximum=database.LIMITS["database_bytes"],
        size=expected_size,
        expected_hash=expected_sha256,
        guard=check,
    )
    worker = _resource(
        "portable_semantics_worker.py", WORKER_SHA256, LIMITS["worker_bytes"], source=True
    )
    base, codec, _ = database._sources(check)
    payload = _resource("portable_payload.py", PAYLOAD_SHA256, 32 * 1024, source=True)
    artifacts = _resource("provenance_artifacts.py", ARTIFACTS_SHA256, 32 * 1024, source=True)
    capture_source = _resource("local_asr_capture.py", CAPTURE_SHA256, 32 * 1024, source=True)
    inventory = _resource("portable_inventory.py", INVENTORY_SHA256, 32 * 1024, source=True)
    transcripts = _resource("transcripts.py", TRANSCRIPTS_SHA256, 64 * 1024, source=True)
    timing = _resource("timing_capture.py", TIMING_SHA256, 64 * 1024, source=True)
    runtime = _resource("runtime_capture.py", RUNTIME_SHA256, 32 * 1024, source=True)
    provider = _resource("provider_capture.py", PROVIDER_SHA256, 48 * 1024, source=True)
    native = _resource("native_capture.py", NATIVE_SHA256, 48 * 1024, source=True)
    raw_catalog = _resource(
        f"catalogs/portable_schema_{version}.json", CATALOGS[version][0], 128 * 1024
    )
    target_catalog = _resource("catalogs/portable_schema_19.json", CATALOGS[19][0], 128 * 1024)
    request = database._canonical(
        {
            "protocol": PROTOCOL,
            "path": str(path),
            "expected_sha256": expected_sha256,
            "expected_size": expected_size,
            "manifest": frozen,
            "deadline_ns": deadline,
            "source_witness": dict(
                zip(
                    ("dev", "ino", "size", "mtime_ns", "ctime_ns"),
                    (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                        before.st_ctime_ns,
                    ),
                    strict=True,
                )
            ),
            "base_source_b64": base64.b64encode(base).decode("ascii"),
            "codec_source_b64": base64.b64encode(codec).decode("ascii"),
            "payload_source_b64": base64.b64encode(payload).decode("ascii"),
            "artifacts_source_b64": base64.b64encode(artifacts).decode("ascii"),
            "capture_source_b64": base64.b64encode(capture_source).decode("ascii"),
            "catalog_b64": base64.b64encode(raw_catalog).decode("ascii"),
            "restore": _restore,
            "target_catalog_b64": base64.b64encode(target_catalog).decode("ascii"),
            "inventory_source_b64": base64.b64encode(inventory).decode("ascii"),
            "preservation_records": preservation_records,
            "transcripts_source_b64": base64.b64encode(transcripts).decode("ascii"),
            "timing_source_b64": base64.b64encode(timing).decode("ascii"),
            "objects_root": str(object_root),
            "runtime_source_b64": base64.b64encode(runtime).decode("ascii"),
            "provider_source_b64": base64.b64encode(provider).decode("ascii"),
            "native_source_b64": base64.b64encode(native).decode("ascii"),
        }
    )
    if len(request) > LIMITS["request_bytes"]:
        _invalid("request size")
    reply = transport._canonical(
        database._run_child(
            worker, request, check, bootstrap=_bootstrap(), reply_bytes=LIMITS["reply_bytes"]
        )
    )
    if (
        type(reply) is not dict
        or reply.get("protocol") != PROTOCOL
        or reply.get("request_sha256") != hashlib.sha256(request).hexdigest()
    ):
        _invalid("reply binding")
    if reply.get("status") == "error":
        if set(reply) != {"protocol", "request_sha256", "status", "code"} or reply["code"] not in {
            "protocol",
            "input",
            "unsupported_runtime",
            "changed_input",
            "schema",
            "integrity",
            "foreign_keys",
            "claims",
            "resource",
            "timeout",
            "processing",
            "semantics",
            "mapping",
        }:
            _invalid("error reply")
        if reply["code"] == "timeout":
            raise TimeoutError("The portable research inspection exceeded its time budget.")
        if reply["code"] == "resource":
            raise ValueError(
                "The portable research inspection exceeds the supported resource budget."
            )
        _invalid("inspection (" + reply["code"] + ")")
    if set(reply) != {"protocol", "request_sha256", "status", "result"} or reply["status"] != "ok":
        _invalid("reply fields")
    result = reply["result"]
    if (
        type(result) is not dict
        or set(result)
        != {
            "schema_version",
            "schema_fingerprint",
            "tables",
            "semantic_counts",
            "required_audio",
            "sqlite_version",
            "heap_cap_bytes",
            "heap_sentry",
            "policy",
        }
        | ({"working_copy"} if _restore is not None else set())
        or result["schema_version"] != version
        or result["schema_fingerprint"] != claims["schema_fingerprint"]
        or result["policy"] != POLICY
        or result["heap_cap_bytes"] != database.HEAP_BYTES
        or result["heap_sentry"] != "allocation_refused_before_open"
        or set(result["tables"]) != set(claims["tables"])
        or any(
            {key: item[key] for key in ("rows", "sha256")} != claims["tables"][name]
            for name, item in result["tables"].items()
        )
    ):
        _invalid("result binding")
    if type(result["required_audio"]) is not list or len(result["required_audio"]) > 20_000:
        _invalid("audio observations")
    for audio in result["required_audio"]:
        if type(audio) is not dict or set(audio) != {"sha256", "frames"}:
            _invalid("audio observation fields")
        database._sha(audio["sha256"])
        database._integer(audio["frames"], 16000 * 21600, 1)
        check()
        if _restore is None:
            _audio_geometry(object_root / audio["sha256"], object_root, audio["frames"])
    check()
    database._sidecars(path)
    _, after = transport._read(
        path,
        parent,
        maximum=database.LIMITS["database_bytes"],
        size=expected_size,
        expected_hash=expected_sha256,
        guard=check,
    )
    transport._same_path(before, after)
    database._sidecars(path)
    return {
        "level": "database_research_consistency",
        "database_sha256": expected_sha256,
        "database_bytes": expected_size,
        **result,
        "worker_sha256_lf": WORKER_SHA256,
        "catalog_sha256": CATALOGS[version][0],
        "restore_ready": False,
        "not_checked": list(NOT_CHECKED),
    }


def prepare_restore(
    source,
    working,
    *,
    expected_sha256,
    expected_size,
    manifest,
    target_root,
    restore_id,
    guard=None,
    artifact_object_root=None,
):
    """Create an isolated working database; file checks and activation stay caller-owned.

    The caller must first inspect the complete package including prepared-audio geometry, then
    materialize and checksum its files. This operation preserves the immutable source object.
    """
    restore = {
        "working_path": str(Path(working)),
        "target_root": str(Path(target_root)),
        "restore_id": transport._hex(restore_id, 32),
        "source_backup_id": manifest["backup_id"],
    }
    observed = inspect_database(
        source,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        manifest=manifest,
        guard=guard,
        _restore=restore,
        artifact_object_root=artifact_object_root,
    )
    result = observed["working_copy"]
    if (
        type(result) is not dict
        or set(result)
        != {
            "working_database_sha256",
            "working_database_bytes",
            "schema_version",
            "quarantine_policy",
            "quarantine_counts",
            "overlay_tables",
        }
        or result["schema_version"] != 19
        or result["quarantine_policy"] != "cbs-restored-owner-quarantine-v1"
    ):
        _invalid("working copy receipt")
    database._sha(result["working_database_sha256"])
    database._integer(result["working_database_bytes"], database.LIMITS["database_bytes"], 1)
    target = Path(working)
    transport._read(
        target,
        target.parent,
        maximum=database.LIMITS["database_bytes"],
        size=result["working_database_bytes"],
        expected_hash=result["working_database_sha256"],
        guard=guard,
    )
    database._sidecars(target)
    return result
