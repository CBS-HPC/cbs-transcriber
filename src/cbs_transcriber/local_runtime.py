"""One cached-model subprocess for bounded passage repairs; never an HTTP service."""

import json
import os
import subprocess
import threading

from . import enhancement


class LocalPassageRuntime:
    def __init__(self, idle_seconds=300):
        self.idle_seconds = idle_seconds
        self.lock = threading.RLock()
        self.process = None
        self.identity = None
        self.timer = None
        self.generation = 0

    def close(self):
        with self.lock:
            self.generation += 1
            if self.timer:
                self.timer.cancel()
            if self.process:
                enhancement.stop_process(self.process)
                if self.process.stdin:
                    self.process.stdin.close()
            self.process = self.identity = None

    def expire(self, generation):
        with self.lock:
            if generation == self.generation:
                self.close()

    def run(self, folder, options, cancel, progress):
        with self.lock:
            identity = (enhancement.python_path(options), options.language, options.device)
            if self.process and (self.identity != identity or self.process.poll() is not None):
                self.close()
            self.generation += 1
            if self.timer:
                self.timer.cancel()
            if cancel.is_set():
                self.close()
                raise enhancement.media.Cancelled()
            if self.process is None:
                self.process = subprocess.Popen(
                    [identity[0], "-I", str(enhancement.WORKER), "--serve-passages"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=enhancement.child_environment(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                self.identity = identity
            for name in ("done.json", "error.json", "progress.json"):
                (folder / name).unlink(missing_ok=True)
            try:
                self.process.stdin.write((json.dumps(str(folder.resolve())) + "\n").encode())
                self.process.stdin.flush()
                enhancement.monitor_worker(
                    self.process, folder, options, cancel, progress, persistent=True
                )
            except BaseException:
                self.close()
                raise
            self.timer = threading.Timer(self.idle_seconds, self.expire, args=(self.generation,))
            self.timer.daemon = True
            self.timer.start()
