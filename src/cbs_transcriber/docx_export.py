"""Minimal Word (.docx) transcript for collaborative review; standard library only.

One paragraph per passage: bold start time and speaker, then the current text. Untimed passages
are included without a time. The package is deterministic so equal transcripts give equal bytes.
"""

import io
import re
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
RELATIONSHIPS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
    'relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
# Characters XML 1.0 cannot carry are dropped from the copy, never from the saved transcript.
INVALID_XML = re.compile("[^\t\n\r -퟿-�\U00010000-\U0010ffff]")


def _clock(seconds):
    whole = int(seconds)
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def _run(text, *, bold=False, size=None):
    properties = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    parts = []
    for index, line in enumerate(INVALID_XML.sub("", text).split("\n")):
        if index:
            parts.append("<w:br/>")
        for position, piece in enumerate(line.split("\t")):
            if position:
                parts.append("<w:tab/>")
            if piece:
                parts.append(f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
    return f"<w:r>{f'<w:rPr>{properties}</w:rPr>' if properties else ''}{''.join(parts)}</w:r>"


def _paragraph(*runs):
    return f"<w:p>{''.join(runs)}</w:p>"


def docx_bytes(segments, speakers, title="Transcript"):
    paragraphs = [_paragraph(_run(title, bold=True, size=32))]
    for segment in segments:
        speaker = speakers.get(segment["speaker"], segment["speaker"])
        start = segment.get("start")
        heading = (f"[{_clock(start)}] " if isinstance(start, (int, float)) else "") + speaker
        paragraphs.append(_paragraph(_run(heading + ": ", bold=True), _run(segment["text"])))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{WORD}"><w:body>{"".join(paragraphs)}<w:sectPr/></w:body>'
        "</w:document>"
    )
    stream = io.BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as package:
        for name, content in (
            ("[Content_Types].xml", CONTENT_TYPES),
            ("_rels/.rels", RELATIONSHIPS),
            ("word/document.xml", document),
        ):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            package.writestr(info, content.encode("utf-8"))
    return stream.getvalue()
