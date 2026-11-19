"""Contained, credential-free Spylls process. Draft words are never logged or persisted."""

import hashlib
import json
import os
import socket
import stat
import sys
from pathlib import Path

MAX_LINE = 512 * 1024


def no_network(*args, **kwargs):
    raise OSError("Spelling is local only")


def verify(path, expected):
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or getattr(before, "st_file_attributes", 0) & 0x400
        or before.st_size != expected["bytes"]
    ):
        raise ValueError("Invalid dictionary")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise ValueError("Dictionary changed")
        while block := stream.read(64 * 1024):
            digest.update(block)
    after = path.lstat()
    if digest.hexdigest() != expected["sha256"] or (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
        raise ValueError("Dictionary changed")


def main():
    socket.socket = no_network
    socket.create_connection = no_network
    from spylls.hunspell import Dictionary

    dictionary, key = None, None
    while line := sys.stdin.buffer.readline(MAX_LINE + 1):
        if len(line) > MAX_LINE or not line.endswith(b"\n"):
            return
        request = None
        try:
            request = json.loads(line)
            identifier = request["id"]
            if request["command"] == "load":
                folder = Path(request["folder"])
                for parent in (*reversed(folder.parents), folder):
                    info = parent.lstat()
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or getattr(info, "st_file_attributes", 0) & 0x400
                    ):
                        raise ValueError("Invalid dictionary folder")
                for kind in ("aff", "dic"):
                    verify(folder / ("dictionary." + kind), request["files"][kind])
                dictionary = Dictionary.from_files(str(folder / "dictionary"))
                for kind in ("aff", "dic"):
                    verify(folder / ("dictionary." + kind), request["files"][kind])
                key = request["key"]
                answer = {"id": identifier, "ready": True}
            elif dictionary is None or request.get("key") != key:
                raise ValueError("Dictionary not loaded")
            elif request["command"] == "check":
                words = request["words"]
                if (
                    not isinstance(words, list)
                    or len(words) > 2048
                    or any(not isinstance(w, str) or len(w) > 128 for w in words)
                ):
                    raise ValueError("Invalid check")
                answer = {"id": identifier, "correct": [dictionary.lookup(word) for word in words]}
            elif request["command"] == "suggest":
                word, count = request["word"], request["count"]
                if (
                    not isinstance(word, str)
                    or not 1 <= len(word) <= 128
                    or type(count) is not int
                    or not 1 <= count <= 5
                ):
                    raise ValueError("Invalid suggestion")
                suggestions = []
                for suggestion in dictionary.suggest(word):
                    if (
                        isinstance(suggestion, str)
                        and len(suggestion) <= 256
                        and suggestion not in suggestions
                    ):
                        suggestions.append(suggestion)
                    if len(suggestions) == count:
                        break
                answer = {"id": identifier, "suggestions": suggestions}
            else:
                raise ValueError("Unsupported command")
        except Exception:
            # Exception messages can contain a private word/path. Never transmit them.
            answer = {
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": "unavailable",
            }
        body = json.dumps(answer, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        if len(body) > MAX_LINE:
            return
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
