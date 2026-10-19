// Expect back (personal-ledger 23): the expectation's page (D3): window.viewExpectation, for #/books/expectation/<id>.
//
// Filled by personal-ledger 35. The page reads GET /api/expectations/<id> once and lays it out:
// the crumbs and the row it sits on, a card with what is still open, how much is back of how much
// expected and for how long, a meter, and the history lines: Marked (Undo the mark), each set-off
// (Unlink), refund lines (Unlink), a let-go (Undo) and the open rest (Let it go…), named only when
// the server names who is still out. Every figure and every action comes from the payload (the
// contract: tests/fixtures/expect/page.json); the page draws a button only where the line lists its
// action, and posts to the contract's URLs (tests/fixtures/expect/routes.json):
//   Undo the mark  DELETE /api/expectations/<id>
//   Unlink         DELETE /api/set-offs/<id>
//   Undo (let-go)  DELETE /api/expectations/<id>/let-go
//   Let it go…     ACT['let-go'] → window.openLetGo(<id>), personal-ledger 37's drawer
// A refusal comes back as the route's words, in a toast; nothing on the page changes.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global ACT, get, act, rerender, esc, money, day, plural */
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};

    /** 'S$ 120' for a whole amount, 'S$ 120.50' otherwise: the head's words, as the case list writes them. */
    function short(minor, cur) { return money(minor, cur).replace(/\.00$/, ''); }
    /** 'KLOOK TRAVEL' → 'Klook': how a line names another item a payment went to. */
    function shortTitle(title) {
        if (!title || /^Opening figure/.test(title) || /^its row/.test(title)) return title || '';
        const first = title.split(/\s+/)[0];
        return first.charAt(0) + first.slice(1).toLowerCase();
    }
    const shortDay = iso => day(iso, { year: false });

    function titleOf(e) { return e.row ? e.row.description : `Opening figure · ${e.opening.name}`; }

    function becameWords(e, letGo) {
        if (letGo.became === 'mine') return 'mine';
        if (e.direction === 'pay') return 'a gift to me';
        return letGo.to ? `a gift to ${letGo.to}` : 'a gift';
    }

    function button(label, action, id, { primary = false } = {}) {
        return `<button type="button" class="btn sm${primary ? '' : ' ghost'}" data-act="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
    }

    // Each line's action, as the server names it, to the button the page draws for it.
    const BUTTONS = {
        undo_mark: line => button('Undo the mark', 'xb-undo-mark', line.id),
        unlink: line => button('Unlink', 'xb-unlink', line.id),
        undo_let_go: (line, e) => button('Undo', 'xb-undo-let-go', e.id),
        let_go: (line, e) => button('Let it go…', 'let-go', e.id, { primary: true }),
    };
    function buttons(line, e) { return (line.actions || []).map(a => (BUTTONS[a] ? BUTTONS[a](line, e) : '')).join(''); }

    function lineHTML(line, e) {
        const cur = line.currency;
        const cells = (when, words, amount, cls = '') =>
            `<div class="xb-hl${cls}"><span class="xb-hl__day">${when}</span><span class="xb-hl__words">${words}</span>`
            + `<span class="xb-hl__num num">${amount}</span><span class="xb-hl__act">${buttons(line, e)}</span></div>`;
        if (line.kind === 'marked') {
            const verb = e.direction === 'pay' ? 'expect to pay back' : 'expect back';
            const of = e.row ? ` of ${esc(money(Math.abs(e.row.amount_minor), e.row.currency))}` : '';
            const who = e.names.length ? ` · ${esc(e.names.map(n => n.name).join(', '))}` : '';
            return cells(esc(shortDay(line.date)), `<b>Marked</b>: ${verb} ${esc(money(line.amount_minor, cur))}${of}${who}`,
                esc(money(line.amount_minor, cur)));
        }
        if (line.kind === 'set_off') {
            const p = line.payment;
            const went = (line.elsewhere || []).map(x => `${short(x.amount_minor, x.currency)} of it went to ${shortTitle(x.title)}`);
            const paid = [short(Math.abs(p.amount_minor), p.currency), ...went].join(', ');
            const who = line.person ? `<b>${esc(line.person)}</b> · ` : '';
            return cells(esc(shortDay(line.date)), `${who}${esc(p.description)} <span class="xb-hl__sub">(${esc(paid)})</span>`,
                esc(money(line.amount_minor, cur)));
        }
        if (line.kind === 'refund') {
            return cells(esc(shortDay(line.date)), `refund of ${esc(shortDay(line.purchase_date))}`, esc(money(line.amount_minor, cur)));
        }
        if (line.kind === 'let_go') {
            const type = [line.display_type, line.book].filter(Boolean).join(' · ');
            return cells(esc(shortDay(line.date)),
                `<b>Let go</b> · ${esc(money(line.amount_minor, cur))} as ${esc(becameWords(e, line))}${type ? `, ${esc(type)}` : ''}`,
                esc(money(line.amount_minor, cur)));
        }
        if (line.kind === 'open') {
            return cells('open', `${esc(money(line.amount_minor, cur))}${line.name ? ` · ${esc(line.name)}` : ''}`, '', ' xb-hl--open');
        }
        return '';
    }

    function headWords(f) {
        const e = f.expectation, cur = e.currency;
        const back = e.direction === 'pay'
            ? `${short(e.set_off_minor, cur)} paid back of ${short(e.amount_minor, cur)} to pay back`
            : `${short(e.set_off_minor, cur)} back of ${short(e.amount_minor, cur)} expected`;
        const letGo = (f.lines || []).find(l => l.kind === 'let_go');
        if (letGo) return { big: 'Closed', sub: `${back} · ${short(letGo.amount_minor, cur)} let go as ${becameWords(e, letGo)}` };
        if (e.open_minor > 0) return { big: `${short(e.open_minor, cur)} still open`, sub: `${back} · open ${plural(f.age_days, 'day')}` };
        return { big: 'Closed', sub: back };
    }

    /** The row the expectation sits on, in a line: day · account · amount · yours, type · book. */
    function rowWords(f) {
        const e = f.expectation;
        if (!e.row) return `${day(e.opening.date)} · an opening figure for ${e.opening.name}`;
        const r = e.row;
        const yours = f.own_minor === null || f.own_minor === undefined ? ''
            : ` · yours ${money(Math.abs(f.own_minor), r.currency)}${r.display_type ? `, ${r.display_type}` : ''} · ${r.book}`;
        return `${day(r.date)} · ${r.account_name} · ${money(Math.abs(r.amount_minor), r.currency)}${yours}`;
    }

    /** The page's HTML from its payload; pure, so it runs under node (tests/test_expect_page.py). */
    function html(f) {
        const e = f.expectation;
        const head = headWords(f);
        const amount = e.amount_minor || 1;
        const backPct = Math.max(0, Math.min(100, f.back_pct));
        const gonePct = Math.max(0, Math.min(100 - backPct, Math.round((100 * (e.let_go_minor || 0)) / amount)));
        const openPct = e.open_minor > 0 ? Math.max(0, 100 - backPct - gonePct) : 0;
        const section = e.direction === 'pay' ? 'To pay back' : 'Expected back';
        const note = e.note ? `<p class="xb-page__note">${esc(e.note)}</p>` : '';
        return `<div class="xb-page">
  <nav class="crumbs" aria-label="Where"><a href="#/books">Books</a> › ${section}</nav>
  <div class="page-head"><div><h1>${esc(titleOf(e))}</h1><p>${esc(rowWords(f))}</p>${note}</div></div>
  <section class="card xb-page__card" aria-labelledby="xb-page-h">
    <div class="xb-page__head"><h2 id="xb-page-h">${esc(head.big)}</h2><p class="xb-page__sub">${esc(head.sub)}</p></div>
    <div class="xb-meter" role="img" aria-label="${esc(`${backPct}% back`)}"><i class="xb-meter__back" style="width:${backPct}%"></i>${gonePct ? `<i class="xb-meter__gone" style="width:${gonePct}%"></i>` : ''}${openPct ? `<i class="xb-meter__open" style="width:${openPct}%"></i>` : ''}</div>
    <div class="xb-hist">
      ${(f.lines || []).map(l => lineHTML(l, e)).join('\n      ')}
    </div>
  </section>
</div>`;
    }

    Expect.page = { html };

    window.viewExpectation = async function viewExpectation(id) {
        return html(await get(`/api/expectations/${id}`, { fresh: true }));
    };

    // The page's buttons; app.js's one click handler finds them by data-act. Loaded beside
    // app.js, ACT is there; read alone (the contract's node harness), there is nothing to wire.
    if (typeof ACT === 'undefined') return;
    ACT['xb-undo-mark'] = async el => {
        const r = await act('DELETE', `/api/expectations/${Number(el.dataset.id)}`, undefined, 'Mark undone');
        if (!r) return;
        if (window.location) window.location.hash = '#/books';
        else rerender();
    };
    ACT['xb-unlink'] = async el => {
        const r = await act('DELETE', `/api/set-offs/${Number(el.dataset.id)}`, undefined, 'Unlinked');
        if (r) rerender();
    };
    ACT['xb-undo-let-go'] = async el => {
        const r = await act('DELETE', `/api/expectations/${Number(el.dataset.id)}/let-go`, undefined, 'Let-go undone');
        if (r) rerender();
    };
})();
