"""The shared browser fixture uses the app's real first-use completion key and dialog."""

import re
from pathlib import Path

from test_browser import FIRST_USE_COMPLETED, RETURNING_BROWSER

STATIC = Path(__file__).parents[1] / "src/cbs_transcriber/static"
APP = STATIC / "app.js"


def test_returning_browser_sets_the_marker_that_skip_sets():
    source = APP.read_text(encoding="utf-8")
    declared = re.findall(r'^const FIRST_USE_MARKER = "([^"]+)";$', source, re.M)
    assert declared == [FIRST_USE_COMPLETED]
    assert f"localStorage.setItem('{FIRST_USE_COMPLETED}', '1')" in RETURNING_BROWSER
    # Skip and Close both complete first use through the same marker.
    assert 'localStorage.setItem(FIRST_USE_MARKER, "1")' in source


def test_returning_browser_keeps_only_the_first_use_modal_closed():
    # The app opens setup with showModal on the dialog the fixture names; other dialogs open.
    source = APP.read_text(encoding="utf-8")
    assert 'const dialog = $("#first-use-dialog");' in source
    assert "if (visible && !dialog.open) dialog.showModal();" in source
    assert 'id="first-use-dialog"' in (STATIC / "index.html").read_text(encoding="utf-8")
    assert "if (this.id !== 'first-use-dialog') return show.call(this);" in RETURNING_BROWSER
