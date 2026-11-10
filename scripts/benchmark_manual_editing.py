"""Small local pilot for transcript editing; all data are synthetic.

Run with the repository's source on PYTHONPATH and an installed Playwright browser.
This does not run inference, use credentials, or connect to UCloud. The temporary
library is independent of the serving application and is removed after the run.
"""

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_browser import LocalApp  # noqa: E402

import cbs_transcriber  # noqa: E402
from cbs_transcriber.transcripts import normalize_segments  # noqa: E402


def main():
    if not Path(cbs_transcriber.__file__).resolve().is_relative_to(ROOT / "src"):
        raise RuntimeError("Set PYTHONPATH=src for this benchmark checkout")
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--words", type=int, default=20)
    parser.add_argument("--timed", action="store_true")
    parser.add_argument("--server-spans", action="store_true")
    parser.add_argument("--structures", action="store_true")
    parser.add_argument("--merge-opens", type=int, default=0)
    args = parser.parse_args()
    if not all(1 <= size <= 3000 for size in args.sizes) or not 1 <= args.samples <= 10:
        parser.error("pilot limits: sizes 1..3000, samples 1..10")
    if not 1 <= args.words <= 29:
        parser.error("pilot limits: words 1..29")
    if args.structures and (not args.timed or min(args.sizes) < 50 or args.words < 2):
        parser.error("structural pilot requires --timed, sizes >= 50 and at least two words")
    if not 0 <= args.merge_opens <= 10:
        parser.error("pilot limits: merge-opens 0..10")
    if args.merge_opens and min(args.sizes) < 2:
        parser.error("merge-open pilot needs at least two passages")

    report = {
        "fixture": "synthetic deterministic Danish/English text",
        "git_revision": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip(),
        "frontend_sha256": hashlib.sha256(
            (ROOT / "src/cbs_transcriber/static/app.js").read_bytes()
        ).hexdigest(),
        "sizes": args.sizes,
        "samples_per_size": args.samples,
        "words_per_passage": args.words,
        "timed_words": args.timed,
        "server_spans": args.server_spans,
        "structural_actions": args.structures,
        "merge_open_samples": args.merge_opens,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "note": "Pilot only: busy-machine timings; history grows with each save.",
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="cbs-edit-pilot-") as temporary:
        app = LocalApp(Path(temporary) / "library")
        app.start()
        try:
            store = app.server.config.app.state.store
            spans = []
            if args.server_spans:
                for name in ("save_transcript", "_append_version", "snapshot_project"):
                    original = getattr(store, name)

                    def measured(*values, _original=original, _name=name, **kwargs):
                        before = time.perf_counter()
                        try:
                            return _original(*values, **kwargs)
                        finally:
                            spans.append((_name, (time.perf_counter() - before) * 1000))

                    setattr(store, name, measured)
            project = store.create_project("Synthetic editing benchmark")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                report["browser"] = browser.version
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                if args.structures:
                    page.on("dialog", lambda dialog: dialog.accept())
                page.add_init_script("""(() => {
                  const original = window.fetch.bind(window);
                  window.fetch = async (url, options = {}) => {
                    if (!['PATCH', 'POST'].includes(options.method) ||
                        !String(url).includes('/segments/'))
                      return original(url, options);
                    const start = performance.now();
                    const result = await original(url, options);
                    window.__benchFetch = {start, response: performance.now()};
                    return result;
                  };
                })()""")
                for size in args.sizes:
                    recording = store.create_recording(project["id"], f"synthetic-{size}.wav")
                    words = ["Hej", "verden", "syntetisk", "tekst", "blue"]
                    passage_words = [words[i % len(words)] for i in range(args.words)]
                    text = " ".join(passage_words) + "."
                    segments = []
                    for index in range(size):
                        segment = {
                            "text": text,
                            "start": index * 3,
                            "end": index * 3 + 2.9,
                            "speaker": f"SPEAKER_0{index % 3}",
                        }
                        if args.timed:
                            segment["words"] = [
                                {
                                    "word": word,
                                    "start": index * 3 + n * 0.1,
                                    "end": index * 3 + n * 0.1 + 0.08,
                                }
                                for n, word in enumerate(passage_words)
                            ]
                        segments.append(segment)
                    store.add_result(
                        recording["id"], normalize_segments(segments), {}, "Synthetic", {}
                    )
                    page.goto(app.url + f"/#/recordings/{recording['id']}", timeout=120000)
                    page.wait_for_selector(f"#passage-{size - 1}", timeout=120000)
                    page.wait_for_function("() => !state.editing && !state.dirty", timeout=120000)
                    if (
                        args.timed
                        and page.locator("#transcript .word").count() != size * args.words
                    ):
                        raise RuntimeError("Synthetic timed-word fixture did not fully render")
                    measurements = []
                    for sample in range(args.samples):
                        # Appends a synthetic history version on every save, as the product does.
                        started = page.evaluate("""() => {
                          const t = performance.now();
                          document.querySelector('#passage-0 [data-action=edit]').click();
                          return {start: t, afterClick: performance.now()};
                        }""")
                        page.wait_for_selector("#passage-0 textarea", timeout=120000)
                        page.locator("#passage-0 textarea").fill(
                            f"Synthetic revision {sample}. " + text
                        )
                        revision = page.evaluate("state.recording.revision")
                        spans.clear()
                        page.evaluate("""() => {
                          window.__benchSave = performance.now();
                          document.querySelector('#passage-0 [data-action=save-edit]').click();
                        }""")
                        page.wait_for_function(
                            "rev => !state.editing && state.recording.revision > rev",
                            arg=revision,
                            timeout=120000,
                        )
                        save_ms = page.evaluate("performance.now() - window.__benchSave")
                        fetch = page.evaluate("window.__benchFetch")
                        measurements.append(
                            {
                                "open_edit_sync_ms": round(
                                    started["afterClick"] - started["start"], 1
                                ),
                                "save_to_observed_settled_ms": round(save_ms, 1),
                                "fetch_to_response_ms": round(
                                    fetch["response"] - fetch["start"], 1
                                ),
                                "after_response_to_observed_ms": round(
                                    save_ms
                                    - (fetch["response"] - page.evaluate("window.__benchSave")),
                                    1,
                                ),
                            }
                        )
                        if args.server_spans:
                            measurements[-1]["server_inclusive_ms"] = {
                                name: round(value, 1) for name, value in spans
                            }
                    run = {
                        "passages": size,
                        "measurements": measurements,
                        "median_open_edit_sync_ms": statistics.median(
                            item["open_edit_sync_ms"] for item in measurements
                        ),
                        "median_save_to_observed_settled_ms": statistics.median(
                            item["save_to_observed_settled_ms"] for item in measurements
                        ),
                    }
                    if args.merge_opens:
                        opens = []
                        for _ in range(args.merge_opens):
                            elapsed = page.evaluate("""() => {
                              const before = performance.now();
                              document.querySelector('#passage-0 [data-action=merge-down]').click();
                              return performance.now() - before;
                            }""")
                            page.wait_for_selector("#passage-merge-dialog[open]", timeout=120000)
                            opens.append(round(elapsed, 1))
                            page.locator("#passage-merge-dialog").evaluate(
                                "element => element.close()"
                            )
                            page.wait_for_selector("#passage-merge-dialog[open]", state="detached")
                        run["merge_open_sync_ms"] = opens
                    if args.structures:

                        def save_structure(action):
                            previous = page.evaluate("state.recording.revision")
                            spans.clear()
                            page.evaluate("window.__benchSave = performance.now()")
                            action()
                            page.wait_for_function(
                                "rev => state.recording.revision > rev && !passageStructurePending",
                                arg=previous,
                                timeout=120000,
                            )
                            elapsed = page.evaluate("performance.now() - window.__benchSave")
                            fetch = page.evaluate("window.__benchFetch")
                            result = {
                                "save_to_observed_settled_ms": round(elapsed, 1),
                                "fetch_to_response_ms": round(
                                    fetch["response"] - fetch["start"], 1
                                ),
                            }
                            if args.server_spans:
                                result["server_inclusive_ms"] = {
                                    name: round(value, 1) for name, value in spans
                                }
                            return result

                        page.locator('#passage-10 [data-action="edit"]').click()
                        page.locator("#passage-10 textarea").evaluate(
                            "element => element.setSelectionRange(4, 4)"
                        )
                        split = save_structure(
                            lambda: page.locator('#passage-10 [data-action="split"]').click()
                        )

                        def merge(segment_id, direction):
                            started = page.evaluate(
                                """([id, direction]) => {
                              const before = performance.now();
                              document.querySelector(
                                `#passage-${id} [data-action=merge-${direction}]`
                              ).click();
                              return performance.now() - before;
                            }""",
                                [segment_id, direction],
                            )
                            page.wait_for_selector("#passage-merge-dialog[open]", timeout=120000)
                            if page.locator("#passage-merge-speaker-label").is_visible():
                                page.locator("#passage-merge-speaker").select_option(index=0)
                            result = save_structure(
                                lambda: (
                                    page.locator("#passage-merge-form")
                                    .get_by_role("button", name="Merge passages", exact=True)
                                    .click()
                                )
                            )
                            result["open_dialog_sync_ms"] = round(started, 1)
                            return result

                        down = merge("20", "down")
                        up = merge("30", "up")
                        deleted = save_structure(
                            lambda: page.locator('#passage-40 [data-action="delete"]').click()
                        )
                        run["structures"] = {
                            "split": split,
                            "merge_down": down,
                            "merge_up": up,
                            "delete": deleted,
                        }
                    report["runs"].append(run)
                browser.close()
        finally:
            app.stop()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
