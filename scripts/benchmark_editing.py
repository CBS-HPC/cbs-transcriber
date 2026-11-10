"""Reproducible editing-speed benchmark on disposable synthetic libraries.

Measures what an editor waits for on long timed transcripts: opening a recording, opening
and saving a passage, speaker rename/assignment, merge/split/delete, Undo merge, History,
Mark Working, playback follow while a passage is open, typing/scrolling responsiveness and
the current-view export. Browser time (input to settled UI plus two frames) is reported
separately from server time (ASGI request span and named Store spans), response sizes and
optional Chromium trace self-time categories.

All text is generated Danish-like synthetic content with a fixed seed; no recordings,
credentials, model downloads or cloud requests are used. The library, its project folders
and the home folder seen by the app are temporary and removed afterwards, so the serving
app and its data are never touched.

    .venv/Scripts/python.exe scripts/benchmark_editing.py --sizes 100 600 1000 --out report.json

Playwright's Chromium must already be installed (`uv run playwright install chromium`).
"""

import argparse
import contextvars
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The app resolves its visible app folder from the home directory (ADR 0050). Point it at a
# disposable folder before importing the package so nothing outside the run is read or written.
_HOME = Path(tempfile.mkdtemp(prefix="cbs-edit-bench-home-"))
os.environ["USERPROFILE"] = os.environ["HOME"] = str(_HOME)
os.environ.setdefault("CBS_TRANSCRIBER_NO_STARTUP_CHECK", "1")
os.environ.setdefault("CBS_TRANSCRIBER_NO_REMOTE_RELEASE", "1")
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import cbs_transcriber  # noqa: E402
from cbs_transcriber import review_hints  # noqa: E402
from cbs_transcriber import store as store_module  # noqa: E402
from cbs_transcriber.app import create_app  # noqa: E402
from cbs_transcriber.transcripts import normalize_segments  # noqa: E402

WORDS = (
    "og det er jo sådan at vi har været meget glade for samarbejdet med kommunen i år "
    "men økonomien blev først afklaret efter mødet på rådhuset hvor borgerne kunne "
    "stille spørgsmål til forvaltningen om skolerne ældreplejen og trafikken café "
    "ja nej altså egentlig præcis derfor tænker jeg måske også lidt anderledes nu"
).split()


class MemoryCredentials:
    def __init__(self):
        self.keys = {}

    def get(self, base_url):
        return self.keys.get(base_url, "")

    def set(self, base_url, value, persist):
        self.keys[base_url] = value


def synthetic_rows(passages, words=15, speakers=3, seed=11):
    """Timed Danish-like passages with review signals, like a native WhisperX result."""
    rng = random.Random(seed)
    rows, clock, speaker = [], 0.0, 0
    for index in range(passages):
        if index and rng.random() < 0.45:
            speaker = (speaker + 1 + rng.randrange(speakers - 1)) % speakers
        tokens = [rng.choice(WORDS) for _ in range(words)]
        if index % 7 == 3:
            tokens[2] = "café"  # Combining mark: exact code-point offsets matter.
        if index % 11 == 5:
            tokens[-1] = tokens[-1] + ","
        timed = []
        for token in tokens:
            length = 0.18 + rng.random() * 0.22
            timed.append(
                {
                    "word": token,
                    "start": round(clock, 3),
                    "end": round(clock + length, 3),
                    "probability": round(0.35 + rng.random() * 0.65, 3),
                }
            )
            clock += length + 0.05
        rows.append(
            {
                "text": " ".join(tokens).capitalize() + ".",
                "start": timed[0]["start"],
                "end": timed[-1]["end"],
                "speaker": f"SPEAKER_{speaker:02d}",
                "words": timed,
                "avg_logprob": round(-0.05 - rng.random() * 0.9, 3),
                "no_speech_prob": round(rng.random() * 0.2, 3),
                "compression_ratio": round(1.2 + rng.random() * 0.8, 3),
            }
        )
        clock += 0.4 + rng.random() * 0.6
    return rows


def percentile(values, share):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * share
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summary(values):
    values = [value for value in values if value is not None]
    if not values:
        return None
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 1),
        "p95": round(percentile(values, 0.95), 1),
        "min": round(min(values), 1),
        "max": round(max(values), 1),
    }


# --- Server instrumentation ----------------------------------------------------------------

REQUEST = contextvars.ContextVar("benchmark_request", default=None)
REQUESTS = []
BACKGROUND_SPANS = []
LOCK = threading.Lock()


class Timing:
    """Server-side span of each HTTP request, including body parsing and serialization."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        entry = {
            "method": scope["method"],
            "path": scope["path"],
            "start": time.perf_counter(),
            "spans": [],
            "bytes": 0,
        }
        token = REQUEST.set(entry)

        async def measured(message):
            if message["type"] == "http.response.start":
                entry["status"] = message["status"]
            if message["type"] == "http.response.body":
                entry["bytes"] += len(message.get("body", b""))
                if not message.get("more_body"):
                    entry["ms"] = (time.perf_counter() - entry["start"]) * 1000
            await send(message)

        try:
            await self.app(scope, receive, measured)
        finally:
            REQUEST.reset(token)
            with LOCK:
                REQUESTS.append(entry)


def spanned(name, function):
    def wrapper(*args, **kwargs):
        before = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - before) * 1000
            entry = REQUEST.get()
            if entry is not None:
                entry["spans"].append((name, elapsed))
            else:
                with LOCK:
                    BACKGROUND_SPANS.append((name, elapsed, time.perf_counter()))

    return wrapper


def instrument(store):
    for name in (
        "save_transcript",
        "snapshot_project",
        "recording",
        "result",
        "_append_version",
        "_head_version",
    ):
        if hasattr(store, name):
            setattr(store, name, spanned(name, getattr(store, name)))
    store_module.encode_payload = spanned("encode_payload", store_module.encode_payload)
    review_hints.current_hints = spanned("current_hints", review_hints.current_hints)
    import fastapi.routing

    original = fastapi.routing.serialize_response

    async def serialize(*args, **kwargs):
        before = time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            entry = REQUEST.get()
            if entry is not None:
                entry["spans"].append(("fastapi_serialize", (time.perf_counter() - before) * 1000))

    fastapi.routing.serialize_response = serialize
    from cbs_transcriber import app as app_module

    if hasattr(app_module, "json_response"):
        app_module.json_response = spanned("json_response", app_module.json_response)


class Server:
    def __init__(self, root):
        import socket

        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{self.socket.getsockname()[1]}"
        self.app = create_app(root, credentials=MemoryCredentials())
        self.store = self.app.state.store
        instrument(self.store)
        self.server = uvicorn.Server(
            uvicorn.Config(
                Timing(self.app), log_level="error", access_log=False, timeout_graceful_shutdown=2
            )
        )
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]}, daemon=True
        )

    def start(self):
        self.thread.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(self.url + "/api/health", timeout=0.5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise RuntimeError("The benchmark server failed to start.")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(20)
        self.socket.close()


def server_requests(since, method=None, contains=None):
    with LOCK:
        rows = REQUESTS[since:]
    return [
        row
        for row in rows
        if (method is None or row["method"] == method)
        and (contains is None or contains in row["path"])
        and "ms" in row
    ]


# --- Browser instrumentation ---------------------------------------------------------------

INIT_SCRIPT = r"""(() => {
  const bench = (window.__bench = {
    requests: [], longtasks: [], events: [], renders: [], inflight: 0,
  });
  window.confirm = () => true;  // Accept delete/discard prompts without native dialogs.
  const rawFetch = window.fetch.bind(window);
  bench.rawFetch = rawFetch;
  window.fetch = async (input, init = {}) => {
    const url = String(input instanceof Request ? input.url : input);
    const tracked = url.includes('/api/recordings/') && !url.includes('/media/');
    const entry = {url, method: String(init.method || 'GET').toUpperCase(),
                   start: performance.now()};
    if (tracked) { bench.inflight++; bench.requests.push(entry); }
    let finished = false;
    const finish = () => { if (tracked && !finished) { finished = true; bench.inflight--; } };
    try {
      const response = await rawFetch(input, init);
      entry.headers = performance.now();
      entry.status = response.status;
      const text = response.text.bind(response);
      response.json = async () => {
        try {
          const body = await text();
          entry.body = performance.now();
          entry.bytes = body.length;
          const value = JSON.parse(body);
          entry.parsed = performance.now();
          // Resolve the caller first, then count the request as done on the next task.
          setTimeout(finish, 0);
          return value;
        } catch (error) { finish(); throw error; }
      };
      if (!tracked) return response;
      const blob = response.blob.bind(response);
      response.blob = async () => { try { return await blob(); } finally { finish(); } };
      response.text = async () => { try { return await text(); } finally { finish(); } };
      return response;
    } catch (error) { finish(); throw error; }
  };
  new PerformanceObserver((list) => {
    for (const e of list.getEntries())
      bench.longtasks.push({start: e.startTime, duration: e.duration});
  }).observe({type: 'longtask', buffered: true});
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries())
        bench.events.push({name: e.name, start: e.startTime, duration: e.duration,
                           processing: e.processingEnd - e.processingStart});
    }).observe({type: 'event', durationThreshold: 16, buffered: true});
  } catch (error) { /* Event Timing unavailable */ }
})();"""

INSTALL_RENDER_PROBE = r"""() => {
  if (window.__benchRenderProbe) return;
  window.__benchRenderProbe = true;
  const original = window.renderTranscript;
  window.renderTranscript = function (...values) {
    const start = performance.now();
    try { return original.apply(this, values); }
    finally { __bench.renders.push({start, duration: performance.now() - start}); }
  };
}"""

MEASURE = r"""async () => {
  const b = window.__bench;
  const r0 = b.requests.length, l0 = b.longtasks.length, d0 = b.renders.length;
  const t0 = performance.now();
  __ACTION__
  const tAction = performance.now();
  const deadline = t0 + __TIMEOUT__;
  await new Promise((resolve, reject) => {
    const check = () => {
      let ok = false;
      try { ok = b.inflight === 0 && !!(__SETTLED__); } catch (error) { ok = false; }
      if (ok) resolve();
      else if (performance.now() > deadline)
        reject(new Error('Timed out: __LABEL__ inflight=' + b.inflight + ' dialogs=' +
          [...document.querySelectorAll('dialog[open]')].map((d) => d.id).join(',') +
          ' status=' + document.querySelector('#history-status')?.textContent + ' ' +
          document.querySelector('#save-status')?.textContent + ' ' + JSON.stringify(
          b.requests.slice(r0).map((e) => [e.url, e.status, !!e.parsed]))));
      else requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  });
  const tSettled = performance.now();
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const tPaint = performance.now();
  await new Promise((r) => setTimeout(r, 80));
  const requests = b.requests.slice(r0).map((e) => ({
    url: e.url.replace(location.origin, ''), method: e.method, status: e.status,
    wait_ms: e.headers - e.start, body_ms: e.body ? e.body - e.headers : null,
    parse_ms: e.parsed ? e.parsed - e.body : null, bytes: e.bytes ?? null,
    started_ms: e.start - t0,
  }));
  const long = b.longtasks.slice(l0).filter((e) => e.start >= t0 - 5 && e.start <= tPaint);
  const renders = b.renders.slice(d0);
  return {
    total_ms: tPaint - t0, sync_ms: tAction - t0, settled_ms: tSettled - t0,
    requests, renders_ms: renders.map((r) => r.duration),
    longest_task_ms: long.reduce((m, e) => Math.max(m, e.duration), 0),
    long_task_ms: long.reduce((s, e) => s + e.duration, 0),
  };
}"""


def measure(page, action, settled, label, timeout=240000):
    script = (
        MEASURE.replace("__ACTION__", action)
        .replace("__SETTLED__", settled)
        .replace("__TIMEOUT__", str(timeout))
        .replace("__LABEL__", label)
    )
    return page.evaluate(script)


def wait(page, expression, timeout=240000):
    page.wait_for_function(
        f"() => !!({expression}) && window.__bench.inflight === 0", timeout=timeout
    )


def main_request(sample, methods=("PATCH", "POST")):
    for request in sample["requests"]:
        if request["method"] in methods:
            return request
    return sample["requests"][0] if sample["requests"] else None


# --- Chromium trace self-time ----------------------------------------------------------------

CATEGORIES = {
    "scripting": {
        "FunctionCall",
        "EvaluateScript",
        "v8.compile",
        "v8.run",
        "TimerFire",
        "EventDispatch",
        "FireAnimationFrame",
        "RunMicrotasks",
        "V8.Execute",
        "v8.callFunction",
        "XHRReadyStateChange",
        "ResourceReceivedData",
    },
    "parse_html": {"ParseHTML", "ParseAuthorStyleSheet"},
    "style": {"UpdateLayoutTree", "RecalculateStyles"},
    "layout": {"Layout", "UpdateLayerTree", "IntersectionObserverController::computeIntersections"},
    "paint": {"Paint", "PrePaint", "Layerize", "UpdateLayer", "CompositeLayers", "Commit"},
    "gc": {"MinorGC", "MajorGC", "BlinkGC.AtomicPhase", "V8.GCScavenger", "V8.GCCompactor"},
}


def trace_breakdown(data):
    events = json.loads(data).get("traceEvents", [])
    main = {
        (e["pid"], e["tid"])
        for e in events
        if e.get("ph") == "M"
        and e.get("name") == "thread_name"
        and e.get("args", {}).get("name") == "CrRendererMain"
    }
    totals = {key: 0.0 for key in CATEGORIES} | {"other": 0.0}
    tasks = 0.0
    by_thread = {}
    for event in events:
        if event.get("ph") == "X" and (event.get("pid"), event.get("tid")) in main:
            by_thread.setdefault((event["pid"], event["tid"]), []).append(event)
    for rows in by_thread.values():
        rows.sort(key=lambda e: (e["ts"], -e.get("dur", 0)))
        stack = []
        selfs = {}
        for event in rows:
            end = event["ts"] + event.get("dur", 0)
            while stack and stack[-1][1] <= event["ts"]:
                stack.pop()
            if stack:
                parent = stack[-1][0]
                selfs[id(parent)] = selfs.get(id(parent), parent.get("dur", 0)) - event.get(
                    "dur", 0
                )
            else:
                tasks += event.get("dur", 0)
            stack.append((event, end))
            selfs.setdefault(id(event), event.get("dur", 0))
        lookup = {id(e): e for e in rows}
        for key, value in selfs.items():
            name = lookup[key]["name"]
            category = next((c for c, names in CATEGORIES.items() if name in names), "other")
            totals[category] += max(value, 0) / 1000
    return {key: round(value, 1) for key, value in totals.items()} | {
        "main_thread_busy_ms": round(tasks / 1000, 1)
    }


# --- Benchmark -------------------------------------------------------------------------------


def seed_recording(store, project_id, passages, words, speakers, seed):
    recording = store.create_recording(project_id, f"synthetic-{passages}.wav")
    segments = normalize_segments(synthetic_rows(passages, words, speakers, seed))
    store.add_result(
        recording["id"],
        segments,
        {},
        "Synthetic native result",
        {"kind": "provider", "capability": "words_speakers", "model": "synthetic"},
        auto_first=True,
    )
    return store.recording(recording["id"])


class Bench:
    def __init__(self, page, server, browser, args):
        self.page, self.server, self.browser, self.args = page, server, browser, args
        self.trace_dir = Path(args.trace_dir) if args.trace_dir else None

    def traced(self, label, run):
        """Run one sample under a Chromium trace and return (sample, breakdown)."""
        if not self.trace_dir:
            return run(), None
        self.browser.start_tracing(
            page=self.page,
            categories=[
                "devtools.timeline",
                "disabled-by-default-devtools.timeline",
                "v8",
                "blink",
            ],
        )
        try:
            sample = run()
        finally:
            data = self.browser.stop_tracing()
        (self.trace_dir / f"{label}.json").write_bytes(data)
        return sample, trace_breakdown(data)

    def segment_ids(self):
        return self.page.evaluate("state.recording.segments.map((s) => s.id)")

    def revision(self):
        return self.page.evaluate("state.recording.revision")

    def run_action(self, label, size, samples, prepare, action, settled, request_methods):
        results, breakdown = [], None
        for index in range(samples):
            context = prepare(index)
            if context is None:
                continue
            act, done = action(context), settled(context)
            since = len(REQUESTS)

            def run(act=act, done=done):
                return measure(self.page, act, done, label)

            if index == 0:
                sample, breakdown = self.traced(f"{label}-{size}", run)
            else:
                sample = run()
            served = [
                row
                for row in server_requests(since)
                if row["method"] in request_methods and "/api/recordings/" in row["path"]
            ]
            sample["server"] = served[0] if served else None
            results.append(sample)
        return self.summarize(results, breakdown)

    @staticmethod
    def summarize(results, breakdown=None):
        if not results:
            return None
        main = [main_request(sample) for sample in results]
        server = [sample["server"] for sample in results if sample.get("server")]
        spans = {}
        for row in server:
            totals = {}
            for name, value in row["spans"]:
                totals[name] = totals.get(name, 0) + value
            for name, value in totals.items():
                spans.setdefault(name, []).append(value)
        renders = [sum(sample["renders_ms"]) for sample in results]
        return {
            "browser_total_ms": summary([s["total_ms"] for s in results]),
            "input_handler_sync_ms": summary([s["sync_ms"] for s in results]),
            "longest_task_ms": summary([s["longest_task_ms"] for s in results]),
            "render_transcript_ms": summary(renders),
            "render_transcript_calls": summary([len(s["renders_ms"]) for s in results]),
            "request_wait_ms": summary([m["wait_ms"] for m in main if m]),
            "response_body_ms": summary([m["body_ms"] for m in main if m]),
            "json_parse_ms": summary([m["parse_ms"] for m in main if m]),
            "response_chars": summary([m["bytes"] for m in main if m and m["bytes"]]),
            "follow_up_requests": summary([max(len(s["requests"]) - 1, 0) for s in results]),
            "server_request_ms": summary([row["ms"] for row in server]),
            "server_response_bytes": summary([row["bytes"] for row in server]),
            "server_spans_ms": {name: summary(values) for name, values in sorted(spans.items())},
            "trace_self_ms": breakdown,
        }

    def open_recording(self, size, recording, project, samples):
        page = self.page

        def prepare(index):
            page.evaluate(f"location.hash = '#/projects/{project}'")
            wait(page, "!state.recording && !document.querySelector('#library-view').hidden")
            return True

        expected = len(recording["segments"])
        return self.run_action(
            "open_recording",
            size,
            samples,
            prepare,
            lambda _: f"location.hash = '#/recordings/{recording['id']}';",
            lambda _: (
                f"state.recording?.id === '{recording['id']}' && "
                f"document.querySelectorAll('#transcript article.passage').length === {expected}"
            ),
            ("GET",),
        )

    def pick(self, offset, index, step=37):
        ids = self.segment_ids()
        return ids[(offset + index * step) % (len(ids) - 2) + 1]

    def open_editor(self, passage):
        self.page.evaluate(
            f"document.querySelector('#passage-{passage} [data-action=edit]').click()"
        )
        wait(self.page, f"document.querySelector('#passage-{passage} textarea')")

    def cancel_editor(self):
        self.page.evaluate(
            "document.querySelector('#transcript [data-action=cancel-edit]')?.click()"
        )
        wait(self.page, "!state.editing")

    def edit_open(self, size, samples):
        opened = []

        def prepare(index):
            if opened:
                self.cancel_editor()
            passage = self.pick(5, index)
            opened.append(passage)
            return passage

        result = self.run_action(
            "open_passage_editor",
            size,
            samples,
            prepare,
            lambda p: f"document.querySelector('#passage-{p} [data-action=edit]').click();",
            lambda p: f"document.querySelector('#passage-{p} textarea')",
            ("GET",),
        )
        self.cancel_editor()
        return result

    def typing(self, size, samples):
        passage = self.pick(9, 0)
        self.open_editor(passage)
        page = self.page
        page.locator(f"#passage-{passage} textarea").focus()
        page.evaluate("window.__bench.events.length = 0; window.__bench.longtasks.length = 0")
        started = time.perf_counter()
        page.keyboard.type(" og så videre" * max(1, samples // 12), delay=15)
        elapsed = (time.perf_counter() - started) * 1000
        page.wait_for_timeout(200)
        data = page.evaluate(
            "({events: __bench.events.filter((e) => ['keydown','keypress','input','keyup']"
            ".includes(e.name)), long: __bench.longtasks})"
        )
        page.evaluate("state.dirty = false")
        self.cancel_editor()
        return {
            "typed_characters": len(" og så videre") * max(1, samples // 12),
            "wall_ms": round(elapsed, 1),
            "slow_key_events": len(data["events"]),
            "event_duration_ms": summary([e["duration"] for e in data["events"]]),
            "longest_task_ms": round(max([e["duration"] for e in data["long"]] or [0]), 1),
        }

    def scrolling(self, size):
        result = self.page.evaluate(r"""async () => {
          const pane = document.querySelector('#transcript');
          const scroller = pane.scrollHeight > pane.clientHeight + 10
            ? pane : document.scrollingElement;
          scroller.scrollTop = 0;
          await new Promise((r) => requestAnimationFrame(r));
          const gaps = [];
          let last = performance.now();
          for (let i = 0; i < 90; i++) {
            scroller.scrollTop += 350;
            await new Promise((r) => requestAnimationFrame(r));
            const now = performance.now();
            gaps.push(now - last);
            last = now;
          }
          scroller.scrollTop = 0;
          return gaps;
        }""")
        return {"frame_gap_ms": summary(result), "frames_over_50ms": sum(g > 50 for g in result)}

    def save_edit(self, size, samples):
        def prepare(index):
            passage = self.pick(3, index, 41)
            self.open_editor(passage)
            text = f"Rettet {index}: " + self.page.evaluate(
                f"state.recording.segments.find((s) => s.id === '{passage}').text"
            )
            self.page.evaluate(
                "([id, text]) => { const input = document.querySelector(`#passage-${id} textarea`);"
                " input.value = text; input.dispatchEvent(new Event('input', {bubbles: true})); }",
                [passage, text],
            )
            return (passage, self.revision())

        return self.run_action(
            "save_passage",
            size,
            samples,
            prepare,
            lambda c: f"document.querySelector('#passage-{c[0]} [data-action=save-edit]').click();",
            lambda c: (
                f"state.recording.revision > {c[1]} && !state.editing && "
                "document.querySelector('#save-status').textContent !== 'Saving…'"
            ),
            ("PATCH",),
        )

    def assign_speaker(self, size, samples):
        page = self.page

        def prepare(index):
            passage = self.pick(7, index, 29)
            page.evaluate(f"document.querySelector('#passage-{passage} .speaker-control').click()")
            wait(page, "document.querySelector('#speaker-dialog').open")
            page.evaluate(
                "() => { const select = document.querySelector('#passage-speaker-select');"
                " const options = [...select.options].filter("
                "(o) => o.value && o.value !== select.value);"
                " select.value = options[0].value; }"
            )
            return (passage, self.revision())

        return self.run_action(
            "assign_speaker",
            size,
            samples,
            prepare,
            lambda c: (
                "document.querySelector('#passage-speaker-form').requestSubmit("
                "document.querySelector('#passage-speaker-save'));"
            ),
            lambda c: (
                f"state.recording.revision > {c[1]} && "
                "!document.querySelector('#speaker-dialog').open"
            ),
            ("PATCH",),
        )

    def rename_speaker(self, size, samples):
        page = self.page

        def prepare(index):
            speaker = page.evaluate(
                f"[...new Set(state.recording.segments.map((s) => s.speaker))][{index} % 2]"
            )
            page.evaluate(f'document.querySelector(`#speakers [data-speaker="{speaker}"]`).click()')
            wait(page, "document.querySelector('#name-dialog').open")
            page.evaluate(f"document.querySelector('#name-input').value = 'Deltager {index}'")
            return (speaker, self.revision())

        return self.run_action(
            "rename_speaker",
            size,
            samples,
            prepare,
            lambda c: "document.querySelector('#name-form').requestSubmit();",
            lambda c: f"state.recording.revision > {c[1]}",
            ("PATCH",),
        )

    def structural(self, size, samples):
        page = self.page
        out = {}
        opens = []

        def prepare_merge(index):
            passage = self.pick(11, index, 43)
            started = page.evaluate(
                "(id) => { const t = performance.now();"
                " document.querySelector(`#passage-${id} [data-action=merge-down]`).click();"
                " return t; }",
                passage,
            )
            wait(page, "document.querySelector('#passage-merge-dialog').open")
            opens.append(page.evaluate("(t) => performance.now() - t", started))
            return (passage, self.revision())

        merged = []

        def merge_action(context):
            merged.append(context[0])
            return (
                "document.querySelector('#passage-merge-form').requestSubmit("
                "[...document.querySelectorAll('#passage-merge-form button')]"
                ".find((b) => b.textContent.trim() === 'Merge passages'));"
            )

        out["merge"] = self.run_action(
            "merge_passages",
            size,
            samples,
            prepare_merge,
            merge_action,
            lambda c: (
                f"state.recording.revision > {c[1]} && !structureLocked() && "
                "!document.querySelector('#passage-merge-dialog').open"
            ),
            ("POST",),
        )
        out["open_merge_dialog_ms"] = summary(opens)

        def prepare_undo(index):
            if index >= len(merged):
                return None
            passage = merged[-1 - index]
            wait(page, f"document.querySelector('#passage-{passage} [data-action=undo-merge]')")
            return (passage, self.revision())

        out["undo_merge"] = self.run_action(
            "undo_merge",
            size,
            min(samples, len(merged)),
            prepare_undo,
            lambda c: (
                f"document.querySelector('#passage-{c[0]} [data-action=undo-merge]').click();"
            ),
            lambda c: f"state.recording.revision > {c[1]} && !structureLocked()",
            ("POST",),
        )

        def prepare_split(index):
            passage = self.pick(13, index, 31)
            self.open_editor(passage)
            page.evaluate(
                "(id) => { const input = document.querySelector(`#passage-${id} textarea`);"
                " let n = 0, i = 0; for (; i < input.value.length; i++)"
                " if (input.value[i] === ' ' && ++n === 6) break;"
                " input.focus(); input.setSelectionRange(i, i); }",
                passage,
            )
            return (passage, self.revision())

        out["split"] = self.run_action(
            "split_passage",
            size,
            samples,
            prepare_split,
            lambda c: f"document.querySelector('#passage-{c[0]} [data-action=split]').click();",
            lambda c: f"state.recording.revision > {c[1]} && !structureLocked() && !state.editing",
            ("POST",),
        )

        def prepare_delete(index):
            return (self.pick(17, index, 47), self.revision())

        out["delete"] = self.run_action(
            "delete_passage",
            size,
            samples,
            prepare_delete,
            lambda c: f"document.querySelector('#passage-{c[0]} [data-action=delete]').click();",
            lambda c: f"state.recording.revision > {c[1]} && !structureLocked()",
            ("POST",),
        )
        return out

    def history_and_working(self, size, samples):
        page = self.page
        out = {}

        def close_history():
            # The dialog's close event is queued; let it run before reopening.
            page.evaluate("document.querySelector('#history-dialog').close()")
            wait(page, "!historyContext")

        out["open_history"] = self.run_action(
            "open_history",
            size,
            samples,
            lambda index: (close_history() if index else None) or True,
            lambda _: "document.querySelector('#history-open').click();",
            lambda _: (
                "document.querySelector('#history-dialog').open && "
                "document.querySelectorAll('#history-versions [data-history-version]').length > 0"
                " && !/Loading/.test(document.querySelector('#history-status').textContent)"
            ),
            ("GET",),
        )
        out["preview_version"] = self.run_action(
            "preview_version",
            size,
            max(1, samples // 2),
            lambda index: True,
            lambda _: (
                "document.querySelector('#history-preview').hidden = true;"
                "document.querySelectorAll('#history-versions [data-history-version]')[1].click();"
            ),
            lambda _: "!document.querySelector('#history-preview').hidden",
            ("GET",),
        )
        marks = []
        for _index in range(max(1, samples // 2)):
            if not page.evaluate("document.querySelector('#history-dialog').open"):
                page.evaluate("document.querySelector('#history-open').click()")
                wait(
                    page,
                    "document.querySelectorAll('#history-versions [data-history-version]')"
                    ".length > 1",
                )
            page.evaluate(
                "document.querySelectorAll('#history-versions [data-history-version]')[1].click()"
            )
            wait(page, "!document.querySelector('#history-preview').hidden")
            page.evaluate("document.querySelector('#history-mark-working').click()")
            wait(page, "document.querySelector('#working-version-dialog').open")
            version = page.evaluate(
                "document.querySelector('#history-mark-working').dataset.version"
            )
            since = len(REQUESTS)
            sample = measure(
                page,
                "document.querySelector('#working-version-apply').click();",
                "!document.querySelector('#working-version-dialog').open && "
                f"state.recording.result_roles?.working?.version_id === '{version}'",
                "mark_working",
            )
            served = [r for r in server_requests(since) if r["method"] == "POST"]
            sample["server"] = served[0] if served else None
            marks.append(sample)
            close_history()
            # Restore the newest version as Working so later edits need no Working prompt.
            page.evaluate("document.querySelector('#working-mark-current').click()")
            wait(page, "document.querySelector('#working-version-dialog').open")
            page.evaluate("document.querySelector('#working-version-apply').click()")
            wait(
                page,
                "!document.querySelector('#working-version-dialog').open && "
                "state.recording.result_roles?.working?.version_id === "
                "state.recording.active_version?.id",
            )
        out["mark_working"] = self.summarize(marks)
        return out

    def follow_while_editing(self, size, samples):
        page = self.page
        passage = self.pick(19, 0)
        self.open_editor(passage)
        rng = random.Random(size)
        targets = page.evaluate(
            "state.recording.segments.filter((s) => s.word_timing && s.words.length > 3)"
            ".map((s) => [s.id, s.words[2].start, s.words[2].end])"
        )
        latencies, worst = [], 0.0
        for _ in range(samples):
            identifier, start, end = rng.choice(targets)
            position = (start + end) / 2
            sample = measure(
                page,
                f"document.querySelector('#audio').currentTime = {position};",
                f"document.querySelector('#passage-{identifier} .word.current[data-word=\"2\"]')"
                f" || document.querySelector('#passage-{identifier}')"
                "?.classList.contains('active')",
                "follow",
                timeout=10000,
            )
            latencies.append(sample["settled_ms"])
            worst = max(worst, sample["longest_task_ms"])
        self.cancel_editor()
        return {"highlight_latency_ms": summary(latencies), "longest_task_ms": round(worst, 1)}

    def export_current(self, size, samples, recording):
        out = {}
        for export_format in ("text", "nvivo-windows", "docx"):
            values, server = [], []
            for _ in range(max(1, samples // 2)):
                since = len(REQUESTS)
                elapsed = self.page.evaluate(
                    "async ([id, result, format]) => { const t = performance.now();"
                    " const r = await __bench.rawFetch(`"
                    "/api/recordings/${id}/export?format=${format}"
                    "&grouping=speaker&result_id=${result}`, {headers: {'X-CBS-Request': '1',"
                    " ...CBSLibraries.instanceHeaders()}}); await r.blob();"
                    " if (!r.ok) throw new Error('export ' + r.status);"
                    " return performance.now() - t; }",
                    [
                        recording,
                        self.page.evaluate("state.recording.active_transcript_id"),
                        export_format,
                    ],
                )
                values.append(elapsed)
                server += [r["ms"] for r in server_requests(since, "GET", "/export")]
            out[export_format] = {
                "browser_ms": summary(values),
                "server_request_ms": summary(server),
            }
        return out


def run_size(args, server, browser, project, size, report):
    store = server.store
    recording = seed_recording(store, project["id"], size, args.words, args.speakers, args.seed)
    first_request = len(REQUESTS)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.add_init_script(INIT_SCRIPT)
    page.set_default_timeout(240000)
    page.on("pageerror", lambda error: print(f"[{size}] page error: {error}", flush=True))
    page.goto(server.url + f"/#/recordings/{recording['id']}")
    wait(page, f"document.querySelectorAll('#transcript article.passage').length === {size}")
    page.evaluate(INSTALL_RENDER_PROBE)
    bench = Bench(page, server, browser, args)
    actions = set(args.actions)
    run = {
        "passages": size,
        "timed_words": sum(len(s["words"]) for s in recording["segments"]),
        "speakers": len({s["speaker"] for s in recording["segments"]}),
    }
    print(f"[{size}] fixture ready", flush=True)
    if "open" in actions:
        run["open_recording"] = bench.open_recording(size, recording, project["id"], args.opens)
        print(f"[{size}] open done", flush=True)
    if "editor" in actions:
        run["open_passage_editor"] = bench.edit_open(size, args.samples)
        run["typing"] = bench.typing(size, args.samples)
        run["scrolling"] = bench.scrolling(size)
        print(f"[{size}] editor/typing/scroll done", flush=True)
    if "save" in actions:
        run["save_passage"] = bench.save_edit(size, args.samples)
        print(f"[{size}] save done", flush=True)
    if "speakers" in actions:
        run["rename_speaker"] = bench.rename_speaker(size, args.structural_samples)
        run["assign_speaker"] = bench.assign_speaker(size, args.structural_samples)
        print(f"[{size}] speakers done", flush=True)
    if "structure" in actions:
        run.update(bench.structural(size, args.structural_samples))
        print(f"[{size}] structure done", flush=True)
    if "history" in actions:
        run.update(bench.history_and_working(size, args.structural_samples))
        print(f"[{size}] history/working done", flush=True)
    if "follow" in actions:
        run["follow_while_editing"] = bench.follow_while_editing(size, args.samples)
        print(f"[{size}] follow done", flush=True)
    if "export" in actions:
        run["export_current_view"] = bench.export_current(size, args.samples, recording["id"])
        print(f"[{size}] export done", flush=True)
    polls = {}
    for row in server_requests(first_request):
        if "/api/recordings/" not in row["path"] and row["path"].startswith("/api/"):
            polls.setdefault(row["method"] + " " + row["path"], []).append(row["ms"])
    run["other_api_requests_ms"] = {key: summary(values) for key, values in sorted(polls.items())}
    run["history_versions_after_run"] = store.one(
        "SELECT COUNT(*) AS n FROM result_versions WHERE result_id=?",
        (store.recording(recording["id"])["active_transcript_id"],),
    )["n"]
    page.close()
    report["runs"].append(run)


def main():
    if not Path(cbs_transcriber.__file__).resolve().is_relative_to(ROOT / "src"):
        raise RuntimeError("Run this benchmark from its own checkout.")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 600, 1000])
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--structural-samples", type=int, default=8)
    parser.add_argument("--opens", type=int, default=6)
    parser.add_argument("--words", type=int, default=15)
    parser.add_argument("--speakers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument(
        "--actions",
        nargs="+",
        default=["open", "editor", "save", "speakers", "structure", "history", "follow", "export"],
    )
    parser.add_argument("--label", default="")
    parser.add_argument("--out")
    parser.add_argument("--trace-dir", help="Write one Chromium trace per action and size")
    args = parser.parse_args()
    if not all(10 <= size <= 3000 for size in args.sizes):
        parser.error("sizes must be 10..3000 passages")
    if not 1 <= args.samples <= 200 or not 1 <= args.structural_samples <= 50:
        parser.error("samples 1..200, structural samples 1..50")
    if not 4 <= args.words <= 40 or not 2 <= args.speakers <= 6:
        parser.error("words 4..40, speakers 2..6")
    if args.trace_dir:
        Path(args.trace_dir).mkdir(parents=True, exist_ok=True)

    def git(*command):
        try:
            return subprocess.run(
                ["git", *command], cwd=ROOT, check=True, capture_output=True, text=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    report = {
        "label": args.label,
        "fixture": "synthetic Danish-like timed passages with review signals (seeded)",
        # An exported copy (git archive) has no repository; name its revision explicitly.
        "git_revision": os.environ.get("BENCH_REVISION") or git("rev-parse", "HEAD"),
        "git_dirty": bool(git("status", "--porcelain", "--", "src")),
        "arguments": vars(args),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runs": [],
    }
    library = Path(tempfile.mkdtemp(prefix="cbs-edit-bench-"))
    server = Server(library / "library")
    server.start()
    try:
        project = server.store.create_project("Synthetic editing benchmark")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            report["browser"] = browser.version
            for size in args.sizes:
                run_size(args, server, browser, project, size, report)
            browser.close()
    finally:
        server.stop()
        shutil.rmtree(library, ignore_errors=True)
        shutil.rmtree(_HOME, ignore_errors=True)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
