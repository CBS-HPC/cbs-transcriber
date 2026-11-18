"""The shared browser fixture uses the app's real first-use completion key."""

import re
from pathlib import Path

from test_browser import FIRST_USE_COMPLETED, RETURNING_BROWSER

APP = Path(__file__).parents[1] / "src/cbs_transcriber/static/app.js"


def test_returning_browser_sets_the_marker_that_skip_sets():
    source = APP.read_text(encoding="utf-8")
    declared = re.findall(r'^const FIRST_USE_MARKER = "([^"]+)";$', source, re.M)
    assert declared == [FIRST_USE_COMPLETED]
    assert f"localStorage.setItem('{FIRST_USE_COMPLETED}', '1')" in RETURNING_BROWSER
    # Skip and Close both complete first use through the same marker.
    assert 'localStorage.setItem(FIRST_USE_MARKER, "1")' in source
