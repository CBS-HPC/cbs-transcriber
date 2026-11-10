"""Exercise the real chooser's restart polling with synthetic responses only."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_library_switch_waits_for_exact_target_and_reports_timeout():
    node = shutil.which("node")
    if node is None:
        pytest.skip("The chooser probe requires Node.js.")
    chooser = Path(__file__).parents[1] / "src" / "cbs_transcriber" / "static" / "library.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let source = fs.readFileSync(process.argv[1], 'utf8');
assert(source.includes('return { bind, request, instanceHeaders, resource, stale };'));
source = source.replace('return { bind, request, instanceHeaders, resource, stale };',
  'return { bind, request, instanceHeaders, resource, stale, open };');
source += '\nglobalThis.chooser = CBSLibraries;';

async function run(storageResponses, reloadAllowed = true, openReply = 'ok') {
  let now = 0, polls = 0, current = true, status = '';
  const location = {href: '/unchanged'};
  const context = {
    document: {body: {dataset: {}}}, location, AbortController,
    Date: {now: () => now},
    setTimeout: (callback, ms) => {
      if (ms === 500) now += 30000;
      return setTimeout(callback, ms === 500 ? 0 : 2);
    },
    clearTimeout,
    fetch: async (path, options) => {
      if (path === '/api/libraries/open') {
        if (openReply === 'lost') throw new TypeError('connection lost');
        if (openReply === 'hang') return new Promise((resolve, reject) =>
          options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
        if (openReply === 'missing') return {ok: true, json: async () => ({})};
        if (openReply !== 'ok')
          return {ok: false, status: openReply, json: async () => ({detail: 'Server refused'})};
        return {ok: true, json: async () => ({library: '/target'})};
      }
      assert.equal(path, '/api/library/switch-ready?expected=%2Ftarget');
      const reply = storageResponses[Math.min(polls++, storageResponses.length - 1)];
      if (reply === 'closed') {
        await new Promise(resolve => setTimeout(resolve, 2));
        current = false; // The dialog closed and a new draft can now be made.
        return {ok: true, json: async () => ({ready: true})};
      }
      if (reply === 'hang') return new Promise((resolve, reject) =>
        options.signal.addEventListener('abort', () => reject(new Error('aborted'))));
      if (reply === 'offline') throw new Error('offline');
      return {ok: true, json: async () => ({ready: reply === '/target'})};
    },
  };
  vm.runInNewContext(source, context);
  let error;
  try {
    await context.chooser.open('/target', false, text => { status = text; },
      () => current, () => reloadAllowed);
  }
  catch (caught) { error = caught; }
  return {location: location.href, polls, error, status};
}
(async () => {
  const success = await run(['/old', '/target']);
  assert.equal(success.location, '/');
  assert.equal(success.polls, 2);
  assert.equal(success.error, undefined);
  const closed = await run(['closed']);
  assert.equal(closed.location, '/unchanged');
  assert.equal(closed.error, undefined);
  const draft = await run(['/target'], false);
  assert.equal(draft.location, '/unchanged');
  assert.match(draft.status, /changes are safe/);
  for (const reply of ['lost', 'hang', 'missing', 408, 500]) {
    const uncertain = await run(['/target'], true, reply);
    assert.equal(uncertain.location, '/unchanged');
    assert.equal(uncertain.polls, 0);
    assert.match(uncertain.error.message, /outcome is unknown/);
  }
  const refused = await run(['/target'], true, 409);
  assert.match(refused.error.message, /Server refused/);
  for (const responses of [['/old'], ['offline'], ['hang']]) {
    const result = await run(responses);
    assert.equal(result.location, '/unchanged');
    assert.equal(result.polls, 2);
    assert.match(result.error.message, /selected library has not responded/);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run([node, "-e", script, str(chooser)], check=True, timeout=10)
