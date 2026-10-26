"""A big drop on Import is read a group of files at a time (2026-10-26).

The operator dropped 64 statements at once and Import showed "Reading..." for
twenty minutes: one request carried every file, past what the server takes in
one go (10 MB) and longer than the hosting proxy waits. The page now reads a
drop in groups of at most IMPORT_GROUP_FILES files and IMPORT_GROUP_BYTES
bytes. Each group is previewed and confirmed as before, one change in Changes
each; confirming a group reads the next. Discard drops the rest too. A
request the server turns away for size, or one that takes too long, ends the
reading with words saying so, never a page left reading.

The real static/app.js runs under node; `fetch` is a stub that records what
each request carried and answers as the server would. Every file is invented.
"""

from __future__ import annotations

import json
import subprocess

from test_client_seams import NODE, needs_node
from test_spending_net import APP_JS_PATH

HARNESS = r"""
const vm = require('vm');
const SENT = [], TOASTS = [];
const viewEl = {};
class Form { constructor() { this.keys = []; } append(k, v) { this.keys.push([k, v && v.name !== undefined ? v.name : String(v)]); } }
const reply = (status, body) => ({ ok: status < 400, status, json: async () => { if (typeof body === 'string') throw new Error('not json'); return body; }, headers: { get: () => null } });
let answer = null;
const ctx = {
  console, Intl, URLSearchParams, FormData: Form, innerWidth: 1366, setImmediate,
  setTimeout: (f, ms) => (ms >= 60000 ? (ctx.__late.push(f), 0) : setTimeout(f, ms)), clearTimeout() {}, __late: [],
  AbortController: class { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; if (this.signal.onabort) this.signal.onabort(); } },
  document: { addEventListener() {}, querySelectorAll: () => [], documentElement: {}, querySelector: s => (s === '#view' ? viewEl : null), getElementById: () => null, createElement: () => ({}) },
  localStorage: { getItem: () => null, setItem() {} }, location: { hash: '#/books/import' },
  matchMedia: () => ({ matches: false, addEventListener() {} }), getComputedStyle: () => ({ getPropertyValue: () => '' }),
  addEventListener() {}, scrollTo() {},
  fetch: (url, opts = {}) => {
    if (!opts.method || opts.method === 'GET') return Promise.resolve(reply(200, String(url).includes('coverage') ? null : []));
    const sent = { url, files: opts.body && opts.body.keys ? opts.body.keys.filter(k => k[0] === 'files').map(k => k[1]) : null };
    SENT.push(sent);
    const got = answer(sent);
    if (got === 'hang') return new Promise((_, no) => { opts.signal.onabort = () => no(new Error('aborted')); });
    return Promise.resolve(got);
  },
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(require('fs').readFileSync(process.argv[2], 'utf8'), ctx, { filename: 'app.js' });
vm.runInContext('toast = (m) => __toasts.push(m); rerender = async () => { __view.html = await viewImport(); };', Object.assign(ctx, { __toasts: TOASTS, __view: viewEl }));
const preview = n => ({ import_id: n, groups: [], total: 0, errors: [], figures: [] });
const files = (n, size = 100000) => Array.from({ length: n }, (_, i) => ({ name: `Sample Bank ${String(i + 1).padStart(2, '0')}.pdf`, size }));
(async () => {
  const out = await vm.runInContext('(async () => {' + process.argv[3] + '})()', Object.assign(ctx, {
    __sent: SENT, __files: files, __preview: preview, __reply: reply, __answer: f => { answer = f; },
  }));
  process.stdout.write(JSON.stringify(out));
})().catch(e => { console.error(e && e.stack || e); process.exit(1); });
"""


def run(body: str):
    done = subprocess.run([NODE, "-", str(APP_JS_PATH), body], input=HARNESS, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


CONFIRM_ALL = """
  let n = 0;
  __answer(s => s.url.includes('confirm') ? __reply(200, {transactions_saved: 3}) : __reply(200, __preview(++n)));
  await uploadFiles(__files(20));
  const seen = [];
  while (importState.preview) {
    await rerender();
    seen.push(__view.html.match(/Files \\d+–\\d+ of \\d+/)?.[0] || null);
    await ACT['import-confirm']({ disabled: false });
  }
  return { sent: __sent.map(s => s.files ? s.files.length : 'confirm'), seen, busy: importState.busy, done: importState.done };
"""


@needs_node
def test_a_drop_of_twenty_is_read_eight_at_a_time_and_each_confirm_reads_the_next():
    got = run(CONFIRM_ALL)

    assert got["sent"] == [8, "confirm", 8, "confirm", 4, "confirm"]
    assert got["seen"] == ["Files 1–8 of 20", "Files 9–16 of 20", "Files 17–20 of 20"]
    assert got["busy"] is False and got["done"].startswith("Imported 3 rows")


@needs_node
def test_a_group_holds_no_more_bytes_than_the_server_takes():
    got = run("""
      __answer(() => __reply(200, __preview(1)));
      await uploadFiles(__files(5, 3_000_000));
      return __sent.map(s => s.files.length);
    """)

    # 3 MB each: two make 6 MB, a third would pass the 8 MB a group may carry.
    assert got == [2]


@needs_node
def test_discard_drops_the_files_still_to_read():
    got = run("""
      __answer(() => __reply(200, __preview(1)));
      await uploadFiles(__files(20));
      ACT['import-clear']();
      return { sent: __sent.length, queued: (importState.queue || []).length, preview: importState.preview };
    """)

    assert got == {"sent": 1, "queued": 0, "preview": None}


@needs_node
def test_an_upload_too_big_for_the_server_says_so_and_stops_reading():
    got = run("""
      __answer(() => __reply(413, '<h1>Request Entity Too Large</h1>'));
      await uploadFiles(__files(1, 12_000_000));
      return { busy: importState.busy, toasts: __toasts };
    """)

    assert got["busy"] is False
    assert any("10 MB" in t for t in got["toasts"])


@needs_node
def test_a_read_that_takes_too_long_ends_with_words_and_writes_nothing():
    got = run("""
      __answer(() => 'hang');
      const reading = uploadFiles(__files(3));
      await new Promise(r => setImmediate(r));
      __late.forEach(f => f());
      await reading;
      await rerender();
      return { busy: importState.busy, toasts: __toasts, html: __view.html.includes('Reading') };
    """)

    assert got["busy"] is False and got["html"] is False
    assert any("too long" in t and "nothing was written" in t for t in got["toasts"])


@needs_node
def test_a_second_drop_while_a_group_is_read_replaces_the_first_whole():
    got = run("""
      let release;
      __answer(s => s.files.length === 8 ? new Promise(r => { release = () => r(__reply(200, __preview('A'))); })
                                         : __reply(200, __preview('B')));
      const a = uploadFiles(__files(20));
      await uploadFiles(__files(3));
      release(); await a;
      await rerender();
      return { id: importState.preview.import_id, queued: importState.queue.length, words: __view.html.includes('of 20') };
    """)

    # The first drop's late answer is not shown, and nothing of it is left to read.
    assert got == {"id": "B", "queued": 0, "words": False}


@needs_node
def test_the_imported_notice_stays_while_the_next_group_is_read():
    got = run("""
      __answer(s => s.url.includes('confirm') ? __reply(200, {transactions_saved: 5})
                  : __sent.length > 2 ? 'hang' : __reply(200, __preview(1)));
      await uploadFiles(__files(12));
      ACT['import-confirm']({ disabled: false });
      await new Promise(r => setImmediate(r));
      await rerender();
      return { done: importState.done, reading: __view.html.includes('Files 9–12 of 12') };
    """)

    assert got["done"].startswith("Imported 5 rows") and got["reading"] is True


@needs_node
def test_one_file_too_big_is_named_and_skipped_and_the_rest_are_read():
    got = run("""
      const big = { name: 'A big statement.pdf', size: 12_000_000 };
      __answer(s => s.files.includes(big.name) ? __reply(413, 'too large') : __reply(200, __preview(2)));
      await uploadFiles([big, ...__files(3)]);
      await new Promise(r => setImmediate(r));
      return { sent: __sent.map(s => s.files.length), toasts: __toasts, id: importState.preview && importState.preview.import_id };
    """)

    assert got["sent"] == [1, 3] and got["id"] == 2
    assert any("A big statement.pdf" in t and "10 MB" in t for t in got["toasts"])


@needs_node
def test_the_preview_says_how_many_the_next_group_holds():
    got = run("""
      __answer(() => __reply(200, Object.assign(__preview(1), {groups: [{account: 'Sample Bank', currency: 'SGD', transactions: [{date: '2026-09-01', description: 'X', amount_sgd: '1.00', flow_type: 'expense', status: 'untyped', statement: 0}], statements: [{status: 'ties', opening: 0, closing: -100, in: 0, out: 100, opening_date: '2026-09-01'}], tie: 'ties', total: 1, typed: 0, untyped: 1, skipped: 0}]})));
      await uploadFiles(__files(5, 3_000_000));
      await rerender();
      return __view.html.match(/The next [^;]+;/)?.[0] || __view.html.slice(0, 300);
    """)

    assert got == "The next 2 are read when you import these;"


@needs_node
def test_a_group_that_fails_keeps_the_rest_and_offers_to_try_again_or_skip():
    got = run("""
      __answer(s => __sent.length === 1 ? __reply(500, {error: 'Sample failure'}) : __reply(200, __preview(3)));
      await uploadFiles(__files(12));
      await rerender();
      const offered = ['import-retry', 'import-next'].every(a => __view.html.includes(`data-act="${a}"`));
      const queued = importState.queue.length;
      await ACT['import-retry']();
      return { offered, queued, sent: __sent.map(s => s.files.length), id: importState.preview.import_id };
    """)

    assert got == {"offered": True, "queued": 4, "sent": [8, 8], "id": 3}
