# 0136 — Bind browser pages to one library server instance

Status: accepted for the local browser interface; real multi-tab and cross-browser acceptance
remain open.

Switching libraries stops one serving app and starts another on the same loopback URL. Two
libraries can contain the same project or recording IDs. A tab left open on the first library
must not silently read or modify matching data from the next one, and a failed download must
not navigate away from a page containing an unsaved draft.

Each app or startup chooser generates a random in-memory instance value and serves its HTML
with that value. Its JavaScript sends the value with API requests and adds it to direct media
and export links. The server refuses a mismatched value before entering mutation admission.
Requests carrying `Origin` or `Sec-Fetch-Site` without a value are also refused. A visible
notice asks the researcher to copy unsaved work and reload; the app does not discard the draft.
Raw HTML paths redirect to the bound page. One narrow switch-ready endpoint permits the
initiating stale page to ask whether its explicitly accepted target path has opened; it returns
only a boolean, not the target path or library contents.

For compatibility, requests without an instance and without those browser-identifying
headers remain available to existing local API clients. Such clients, and browsers that omit
both headers, are not fenced across a switch. A non-browser integration that sends either
header needs the instance value. The value is a safety marker, not authentication.

Direct downloads check the instance first and use a download anchor instead of navigating the
editor. The server checks again when serving bytes; a switch between those events can produce
an error file on some browsers, not data from the next library. The preflight does not verify
downloaded bytes. Large originals are not buffered into JavaScript. Real launcher/two-tab,
Firefox, Safari and cross-platform download behavior require separate acceptance.

Synthetic checks use two isolated app instances with matching synthetic route IDs and verify
stale reads, writes and direct resources, a retained draft, chooser/reload behavior and fresh
download filenames. No real library, private media or remote service is involved.
