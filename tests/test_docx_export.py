"""Word export for collaborative review: valid package, every passage, no saved-data changes."""

import io
import xml.etree.ElementTree as ET
import zipfile

from cbs_transcriber.exports import export_bytes
from cbs_transcriber.transcripts import normalize_segments

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def passages():
    return normalize_segments(
        [
            {"text": "Hej København 🙂", "start": 65, "end": 67, "speaker": "SPEAKER_00"},
            {"text": "Linje et\nlinje\x01 to", "start": 70, "end": 72, "speaker": "SPEAKER_01"},
            {"text": "Uden tid.", "speaker": "SPEAKER_00"},
        ]
    )


def paragraphs(data):
    package = zipfile.ZipFile(io.BytesIO(data))
    assert {"[Content_Types].xml", "_rels/.rels", "word/document.xml"} <= set(package.namelist())
    root = ET.fromstring(package.read("word/document.xml"))
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")], root


def test_docx_has_one_paragraph_per_passage_with_time_and_speaker():
    segments = passages()
    data = export_bytes(segments, {"SPEAKER_01": "Respondent"}, "speaker", "docx")
    texts, root = paragraphs(data)
    assert texts[0] == "Transcript"
    assert texts[1] == "[00:01:05] Speaker_01: Hej København 🙂"
    assert texts[2] == "[00:01:10] Respondent: Linje etlinje to"
    assert texts[3] == "Speaker_01: Uden tid."
    assert len(list(root.iter(W + "br"))) == 1  # The line break is kept as a Word break.
    assert segments[1]["text"] == "Linje et\nlinje\x01 to"  # Saved text is unchanged.
    assert export_bytes(segments, {"SPEAKER_01": "Respondent"}, "speaker", "docx") == data


def test_docx_download_and_project_archive(client):
    store = client.app.state.store
    project = store.create_project("Word")
    recording = store.create_recording(project["id"], "Interview æøå.wav")
    store.add_result(recording["id"], passages(), {}, "R", {})
    response = client.get(f"/api/recordings/{recording['id']}/export", params={"format": "docx"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert ".docx" in response.headers["content-disposition"]
    assert paragraphs(response.content)[0][1].startswith("[00:01:05]")
    archive = client.get(
        f"/api/projects/{project['id']}/export", params={"format": "docx", "results": "active"}
    )
    assert archive.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert any(name.endswith(".docx") for name in names)
