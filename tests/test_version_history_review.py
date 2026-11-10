"""Independent regression probes for delayed history responses; no app or browser runs."""

import shutil
import subprocess
from pathlib import Path

import pytest
from report_support import selected_view_report


@pytest.mark.parametrize("late_change", ["notes", "navigation", "draft"])
def test_delayed_restore_reply_preserves_newer_notes_view_and_drafts(late_change):
    node = shutil.which("node")
    if node is None:
        pytest.skip("The source-level browser event probe requires an installed Node.js.")
    app = Path(__file__).parents[1] / "src" / "cbs_transcriber" / "static" / "app.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const lateChange = process.argv[2];
const start = source.indexOf('$("#history-restore").addEventListener(');
const end = source.indexOf('\nlet researchReportContext', start);
assert(start >= 0 && end > start, 'The actual restore event handler must be located');
let handler, release;
const button = {dataset: {version: 'earlier', sequence: '1'}, disabled: false};
const nodes = {
  '#history-restore': {addEventListener: (_, callback) => {handler = callback;}},
  '#history-dialog': {close: () => {}},
  '#history-status': {textContent: ''},
};
const context = {epoch: 1, id: 'recording', result: 'result', editVersion: 0};
const state = {recording: {
  id: 'recording', active_transcript_id: 'result', revision: 2,
  notes: 'Earlier saved notes', notes_revision: 0,
}};
const scope = {
  state, historyContext: context, viewEpoch: 1, editVersion: 0,
  $: id => {assert(id in nodes, `Unexpected DOM access ${id}`); return nodes[id];},
  handle: callback => callback,
  currentHistory: current => scope.historyContext === current,
  canDiscardPassage: () => true,
  confirm: () => true,
  api: () => new Promise(resolve => {release = resolve;}),
  clearEdit: () => {state.dirty = false; state.editing = null;},
  renderEditorDetails: () => {}, renderTranscript: () => {}, toast: () => {},
};
vm.createContext(scope);
// Exercise the real recording-acceptance guard and restore event code, not a rewritten handler.
const guardStart = source.indexOf('function sameRecordingView(');
const guardEnd = source.indexOf('\nlet nameResolver', guardStart);
assert(guardStart >= 0 && guardEnd > guardStart);
vm.runInContext(source.slice(guardStart, guardEnd), scope);
vm.runInContext(source.slice(start, end), scope);
(async () => {
  const pending = handler({currentTarget: button});
  assert.equal(typeof release, 'function');
  // Escape closes History while its already committed restore reply is delayed.
  scope.historyContext = null;
  if (lateChange === 'notes') {
    // A separate notes save does not change transcript revision/view identity.
    state.recording.notes = 'Newer successfully saved notes';
    state.recording.notes_revision = 1;
  } else if (lateChange === 'navigation') {
    scope.viewEpoch = 2;
    state.recording = {id: 'other', active_transcript_id: 'other-result', revision: 42,
      notes: 'Other recording notes', notes_revision: 1};
  } else {
    scope.editVersion = 1;
    state.dirty = true;
    state.editing = {text: 'A newer unsaved correction'};
  }
  release({id: 'recording', active_transcript_id: 'result', revision: 3,
    notes: 'Earlier saved notes', notes_revision: 0});
  await pending;
  if (lateChange === 'notes') {
    assert.equal(state.recording.revision, 3, 'Restore still updates transcript state');
    assert.equal(state.recording.notes, 'Newer successfully saved notes');
    assert.equal(state.recording.notes_revision, 1);
  } else if (lateChange === 'navigation') {
    assert.equal(state.recording.id, 'other');
    assert.equal(state.recording.revision, 42);
    assert.equal(state.recording.notes, 'Other recording notes');
  } else {
    assert.equal(state.recording.revision, 2);
    assert.equal(state.dirty, true);
    assert.equal(state.editing.text, 'A newer unsaved correction');
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
"""
    outcome = subprocess.run(
        [node, "-e", script, str(app), late_change],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert outcome.returncode == 0, outcome.stdout + outcome.stderr


@pytest.mark.parametrize("stage", ["whisper-medium", "whisper-large-v3-turbo", "whisper-large-v3"])
def test_report_retains_already_captured_asr_pin_without_new_language_receipts(tmp_path, stage):
    import io
    import json
    from zipfile import ZipFile

    from cbs_transcriber.store import Store
    from cbs_transcriber.transcripts import normalize_segments

    store = Store(tmp_path)
    project = store.create_project("Synthetic captured ASR pin")
    recording = store.create_recording(project["id"], "synthetic.wav")
    pin = {"repo": "Synthetic/faster-" + stage, "revision": "a" * 40}
    result = store.add_result(
        recording["id"],
        normalize_segments([{"text": "Synthetic wording.", "start": 0, "end": 2}]),
        {},
        "Synthetic ASR",
        {
            "kind": "provider",
            "provider_type": "whisperx",
            "model": "whisperx/" + stage.removeprefix("whisper-"),
            # Older successful ASR origins already retain this pin, independently of the
            # subsequently introduced configured/automatic language evidence protocols.
            "model_revisions": {stage: pin},
        },
    )
    origin = store.result_versions(recording["id"], result["id"])[0]
    assert origin["context"]["processing"]["model_revisions"][stage] == pin
    with ZipFile(io.BytesIO(selected_view_report(store, project["id"]))) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        methods = archive.read("methods.md").decode("utf-8")
    assert manifest["versions"][0]["processing"]["model_revisions"][stage] == pin
    assert f"{pin['repo']}@{pin['revision']}" in methods
    assert store.result_versions(recording["id"], result["id"])[0] == origin


def test_report_asr_pin_allowlist_keeps_rejecting_invalid_and_private_metadata():
    import json

    from cbs_transcriber.research_reports import _processing

    safe = {"repo": "Synthetic/valid-model", "revision": "a" * 40}
    observed = {
        "model_revisions": {
            "whisper-medium": {"repo": "../../PRIVATE_PATH", "revision": "a" * 40},
            "whisper-large-v3": {"repo": "Synthetic/valid-model", "revision": "PRIVATE_TOKEN"},
            "whisper-large-v3-turbo": {**safe, "token": "PRIVATE_TOKEN", "path": "PRIVATE_PATH"},
            "PRIVATE_STAGE": {"repo": "Synthetic/unknown-stage", "revision": "b" * 40},
        }
    }
    projected = _processing(observed)
    assert projected == {"model_revisions": {"whisper-large-v3-turbo": safe}}
    assert "PRIVATE_" not in json.dumps(projected)
