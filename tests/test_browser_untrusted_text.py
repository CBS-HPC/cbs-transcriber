"""Actual renderer code in a socket-free browser page; API data comes from synthetic Store."""

import json
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_browser import page as page

from cbs_transcriber.backup import FORMAT

pytestmark = pytest.mark.browser
STATIC = Path(__file__).parents[1] / "src/cbs_transcriber/static"


def section(source, start, end):
    return source[source.index(start) : source.index(end, source.index(start))]


def test_legacy_backup_count_is_rejected_by_reader_and_literal_in_renderer(client, page):
    payload = "0 <span data-cbs-untrusted>synthetic markup</span>"
    folder = client.app.state.store.root / "backups" / "library-synthetic"
    folder.mkdir(parents=True)
    manifest = {
        "format": FORMAT,
        "created": "2026-10-31T00:00:00+00:00",
        "tables": {"recordings": payload},
        "originals": [],
        "missing_originals": [],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    response = client.get("/api/library/backups")
    assert response.status_code == 200, response.text
    listing = response.json()
    assert listing["backups"] == []
    # Probe the renderer independently with the exact hostile metadata refused above.
    listing["backups"] = [
        {
            "folder": str(folder),
            "created": manifest["created"],
            "recordings": payload,
            "originals": 0,
            "missing_originals": 0,
        }
    ]
    page.route("**/*", lambda route: route.abort())
    page.set_content('<details id="legacy-backups"><ul id="library-backups"></ul></details>')
    page.evaluate(
        """listing => {
          window.$ = selector => document.querySelector(selector);
          window.api = async () => listing;
        }""",
        listing,
    )
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    script = section(source, "const h = (value) =>", "const state =") + section(
        source, "async function renderBackups() {", '$("#settings-provider").addEventListener('
    )
    page.add_script_tag(content=script)
    page.evaluate("renderBackups()")
    expect(page.locator("[data-cbs-untrusted]")).to_have_count(0)
    expect(page.locator("#library-backups")).to_contain_text(payload)


def test_library_paths_and_errors_are_literal_in_the_actual_chooser_script(page):
    payload = '<img data-cbs-untrusted src=x onerror="window.untrustedRan=true"> æøå'
    page.route("**/*", lambda route: route.abort())
    page.set_content(
        '<div id="panel"><ul data-library-list></ul><button data-library-new>New</button>'
        "<p data-library-status></p></div>"
    )
    page.evaluate(
        """payload => {
          window.untrustedRan=false;
          window.fetch=async () => ({ok:true,json:async()=>({
            can_switch:true,default_new:payload,
            libraries:[{path:payload,kind:payload,exists:true,projects:1,recordings:2}]
          })});
        }""",
        payload,
    )
    page.add_script_tag(path=STATIC / "library.js")
    page.evaluate("CBSLibraries.bind(document.querySelector('#panel')).load()")
    expect(page.locator("[data-cbs-untrusted]")).to_have_count(0)
    expect(page.locator(".library-path")).to_have_text(payload)
    expect(page.locator(".library-item strong")).to_have_text(payload)
    page.evaluate(
        "payload => { window.fetch=async () => ({ok:false,json:async()=>({detail:payload})}); }",
        payload,
    )
    page.evaluate(
        """async () => {
          try { await CBSLibraries.request('/synthetic-error'); }
          catch(error) {
            CBSLibraries.bind(document.querySelector('#panel')).status(error.message, true);
          }
        }"""
    )
    expect(page.locator("[data-library-status]")).to_have_text(payload)
    expect(page.locator("[data-cbs-untrusted]")).to_have_count(0)
    assert page.evaluate("window.untrustedRan") is False
