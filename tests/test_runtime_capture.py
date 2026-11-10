"""Pure Runtime receipt tests; runnable without app, models, network or heavy test fixtures."""

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cbs_transcriber import runtime_capture as capture

REC = "a" * 32
JOB = "b" * 32
IDENTITY = {"operation": "transcription"}
BINDING = {
    "batch_id": "c" * 32,
    "task_id": "d" * 32,
    "attempt": 1,
    "identity_hash": hashlib.sha256(b'{"operation":"transcription"}').hexdigest(),
}
CREATION = {"result_id": "1" * 32, "version_id": "2" * 32, "payload_sha256": "3" * 64}


def receipt(data):
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


class TestRuntimeCapture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        params = {
            "transcription": True,
            "enhancement": {"execution": "ucloud", "transcribe": True, "retranscribe": False},
            "ucloud_api": {"batch_plan": {}},
        }
        self.legacy = copy.deepcopy(params)
        self.job = {
            "id": JOB,
            "recording_id": REC,
            "kind": "enhance",
            "params": capture.freeze(params),
        }
        request = b'{ "options": {"language": "da"}, "passages": [] }\r\n'
        (self.folder / "request.json").write_bytes(request)
        (self.folder / "audio.wav").write_bytes(b"synthetic prepared container")
        self.input_files = {
            "request.json": receipt(request),
            "audio.wav": receipt(b"synthetic prepared container"),
        }
        self.origin = capture.origin(
            self.job, self.folder, binding=BINDING, manifest_sha256="f" * 64, files=self.input_files
        )
        self.inputs = {
            "files": self.input_files,
            "manifest_sha256": "f" * 64,
            "publish_intent": True,
            "artifact_origin": self.origin,
        }
        self.output_files = {}
        for name, data in {
            "transcription.json": b'{"segments":[{"text":"Private words","start":0,"end":1}]}',
            "outcome.json": b'{ "state":"complete" }\r\n',
        }.items():
            (self.folder / name).write_bytes(data)
            self.output_files[name] = receipt(data)
        self.checkpoints = capture.collect(self.origin, self.folder, files=self.output_files)
        self.remote = {
            "files": {"transcription.json": self.output_files["transcription.json"]},
            "outcome": "complete",
            "artifact_files": self.output_files,
            "artifact_checkpoints": self.checkpoints,
        }
        self.value = capture.document(BINDING, self.inputs, self.remote)
        self.output = {
            "batch_id": BINDING["batch_id"],
            "task_id": BINDING["task_id"],
            "attempt": 1,
            "epoch": 4,
            "files": self.output_files,
        }
        self.job["params"]["runtime_output_receipt"] = self.output

    def database(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        db.executescript("""
            CREATE TABLE runtime_batch_runs(id TEXT,compatibility_hash TEXT,identity TEXT);
            CREATE TABLE runtime_batch_members(job_id TEXT,batch_id TEXT,recording_id TEXT,
                current_attempt INTEGER,result_id TEXT);
            CREATE TABLE runtime_batch_attempts(job_id TEXT,attempt INTEGER,task_id TEXT,
                input_receipt TEXT,remote_receipt TEXT);
        """)
        db.execute(
            "INSERT INTO runtime_batch_runs VALUES (?,?,?)",
            (BINDING["batch_id"], BINDING["identity_hash"], json.dumps(IDENTITY)),
        )
        db.execute(
            "INSERT INTO runtime_batch_members VALUES (?,?,?,?,NULL)",
            (JOB, BINDING["batch_id"], REC, 1),
        )
        db.execute(
            "INSERT INTO runtime_batch_attempts VALUES (?,?,?,?,?)",
            (JOB, 1, BINDING["task_id"], json.dumps(self.inputs), json.dumps(self.remote)),
        )
        return db

    def test_exact_bytes_bind_to_creation_without_private_projection(self):
        value = capture.for_creation(self.database(), self.job, REC, self.value, self.output)
        bound = capture.bind(value, creation=CREATION)
        public = capture.public_projection(bound, recording_id=REC, job_id=JOB, creation=CREATION)
        self.assertEqual(
            public["request"]["artifacts"]["request"]["sha256"],
            self.input_files["request.json"]["sha256"],
        )
        self.assertEqual(
            public["outputs"]["task_outcome"]["artifacts"]["output"]["bytes"],
            self.output_files["outcome.json"]["size"],
        )
        self.assertEqual(public["worker_environment"], "not_captured")
        self.assertEqual(public["retained_files"], "not_checked")
        encoded = json.dumps(public)
        for private in (
            REC,
            JOB,
            BINDING["batch_id"],
            str(self.folder),
            "Private words",
            "request.json",
        ):
            self.assertNotIn(private, encoded)
        with self.assertRaises(ValueError):
            capture.public_projection(
                bound,
                recording_id=REC,
                job_id=JOB,
                creation={**CREATION, "payload_sha256": "0" * 64},
            )

    def test_actual_attempt_and_explicit_keyword_are_required(self):
        db = self.database()
        with self.assertRaises(ValueError):
            capture.for_creation(db, self.job, REC, None, self.output)
        db.execute("UPDATE runtime_batch_members SET recording_id=?", ("9" * 32,))
        with self.assertRaises(ValueError):
            capture.for_creation(db, self.job, REC, self.value, self.output)
        missing = self.database()
        missing.execute("UPDATE runtime_batch_attempts SET input_receipt='{}'")
        with self.assertRaises(ValueError):
            capture.for_creation(missing, self.job, REC, None, self.output)

    def test_changed_output_receipt_is_not_accepted(self):
        output = copy.deepcopy(self.output)
        output["files"]["transcription.json"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            capture.for_creation(self.database(), self.job, REC, self.value, output)

    def test_origin_cannot_be_backfilled_after_publication(self):
        with self.assertRaises(ValueError):
            capture.check_update(
                self.job, BINDING, {"publish_intent": True}, {}, self.inputs, {}, terminal=False
            )
        capture.check_update(self.job, BINDING, {}, {}, self.inputs, {}, terminal=False)

    def test_origin_and_stage_observations_are_append_only(self):
        capture.check_update(
            self.job, BINDING, self.inputs, {}, self.inputs, self.remote, terminal=False
        )
        changed = copy.deepcopy(self.inputs)
        changed["artifact_origin"]["generation"] = "0" * 32
        with self.assertRaises(ValueError):
            capture.check_update(
                self.job, BINDING, self.inputs, self.remote, changed, self.remote, terminal=False
            )
        with self.assertRaises(ValueError):
            capture.check_update(
                self.job, BINDING, self.inputs, {}, self.inputs, self.remote, terminal=True
            )

    def test_replaced_bytes_fail_without_deleting_files(self):
        path = self.folder / "transcription.json"
        path.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            capture.collect(self.origin, self.folder, files=self.output_files)
        self.assertEqual(path.read_bytes(), b"changed")

    def test_legacy_absence_does_not_capture_or_require_rows(self):
        legacy = {**self.job, "params": self.legacy}
        self.assertIsNone(
            capture.origin(
                legacy,
                self.folder,
                binding=BINDING,
                manifest_sha256="f" * 64,
                files=self.input_files,
            )
        )
        self.assertIsNone(capture.for_creation(None, None, REC, None))
        self.assertEqual(
            capture.freeze({"enhancement": {"execution": "local"}}),
            {"enhancement": {"execution": "local"}},
        )

    def test_only_fresh_full_asr_is_eligible(self):
        for update in (
            {"source_snapshot": {}},
            {"segment_id": "0"},
            {"retranscription": True},
            {"realign": True},
            {"cloud_settings": {}},
            {"transcription": False},
        ):
            params = {**self.legacy, **update}
            self.assertNotIn("runtime_artifact_policy", capture.freeze(params))

    def test_compressed_input_inventory_resolves_original_separately(self):
        files = {
            "request.json": self.input_files["request.json"],
            "original.input.json": receipt(b"{}"),
            "audio.source.mp4": {"size": 100, "sha256": "7" * 64},
        }
        origin = capture.origin(
            self.job, self.folder, binding=BINDING, manifest_sha256="f" * 64, files=files
        )
        inputs = {**self.inputs, "files": files, "artifact_origin": origin}
        value = capture.document(BINDING, inputs, {})
        inventory = capture.inventory(value, recording_id=REC, job_id=JOB)
        original = next(item for item in inventory if item["scope"] == "managed_original")
        self.assertEqual(original["transport_name"], "audio.source.mp4")
        self.assertNotIn("filename", original)
        self.assertNotIn("audio.wav", [item.get("filename") for item in inventory])


if __name__ == "__main__":
    unittest.main()
