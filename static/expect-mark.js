// Expect back (personal-ledger 23): the shared namespace, and the Expect some back… drawer.
//
// This file loads first of the eight expect-*.js shells (index.html, after app.js), so it
// holds what they share: window.Expect, its slot registry, and the window hooks app.js
// calls with optional chaining. app.js names each place once and draws nothing there until
// a shell fills it, so no lane edits app.js, index.html or styles.css to put a button,
// a pill or a section on a page.
//
//   Expect.fill(slot, fn)   a lane adds what it draws in a slot; slots draw in fill order
//   Expect.draw(slot, ...)  what every filled fn returns, joined ('' when none is filled)
//   Expect.words[table]     the Changes words for one of the seven tables (Expect.TABLES)
//
// Slots and the app.js place that calls each:
//   rowActions(row)          openRowSheet's actions line, after This was…
//   rowPill(row)             the pill after the Claude mark in rowTr, spendRowTr, acctRowTr and merchant rows
//   merchantRowAction(row)   viewMerchant's Rows template, its last cell
//   booksSections(sheet)     viewSheet, between the sheet card and the month check
//   queueStep(list)          the Queue's Money in lane, first in its body (may be async)
//   backForFoot(payment)     the Back for… drawer's footer (expect-setoff.js draws it)
//
// Entry points app.js reserves (ACT['expect-mark'], ACT['back-for'], ACT['let-go']):
//   window.openExpectMark(txId, el), window.openBackFor(txId, el), window.openLetGo(expectationId, el);
//   the route #/books/expectation/<id> calls window.viewExpectation(id).
//
// The drawer itself is personal-ledger 34's (D1); every figure comes from the server's
// payload (tests/fixtures/expect/), never a formula here.
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};
    Expect.slots = Expect.slots || {};
    Expect.words = Expect.words || {};
    Expect.TABLES = ['expectation', 'expectation_name', 'set_off', 'let_go', 'refund_link', 'sender_wording', 'dismissal'];

    Expect.fill = function fill(slot, fn) { (Expect.slots[slot] = Expect.slots[slot] || []).push(fn); };
    Expect.draw = function draw(slot, ...args) { return (Expect.slots[slot] || []).map(fn => fn(...args) || '').join(''); };
    Expect.drawAsync = async function drawAsync(slot, ...args) {
        const parts = await Promise.all((Expect.slots[slot] || []).map(fn => fn(...args)));
        return parts.map(p => p || '').join('');
    };

    window.expectRowActions = row => Expect.draw('rowActions', row);
    window.expectRowPill = row => Expect.draw('rowPill', row);
    window.expectMerchantRowAction = row => Expect.draw('merchantRowAction', row);
    window.expectBooksSections = sheet => Expect.draw('booksSections', sheet);
    window.expectQueueStep = list => Expect.drawAsync('queueStep', list);
    window.expectBackForFoot = payment => Expect.draw('backForFoot', payment);
    window.expectChangeWords = (change, ctx, opts) => {
        const words = Expect.TABLES.includes(change.table) && Expect.words[change.table];
        return (words && words(change, ctx, opts)) || '';
    };
})();

// ---------------------------------------------------------------------------
// D1. Expect some back… / Expect to pay back (personal-ledger 34)
//
// From the row sheet's actions line, a merchant page's rows and anywhere a row offers
// data-act="expect-mark" (the Queue's rows open the row sheet). All of it | Part, a live
// "Yours:" line, people chips with + Someone new, a note. Every figure is the server's:
// GET /api/transactions/<id>/expectation answers what would be yours as the amount is
// typed; POST marks the row or edits its one mark; DELETE /api/expectations/<id> undoes it.
// ---------------------------------------------------------------------------
(function () {
    'use strict';

    const Expect = window.Expect;
    const LONG_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];
    const MOVES = ['transfer', 'payment'];

    /** 'S$ 120' for a whole amount, 'S$ 120.50' otherwise (money() with its .00 dropped). */
    const shortMoney = (minor, cur) => money(minor, cur).replace(/\.00$/, '');
    const what = direction => direction === 'pay' ? 'to pay back' : 'expected back';
    /** Money out or in, from the row the lists hand over; null when it does not say. */
    const lane = row => typeof row.amount_sgd !== 'number' || row.amount_sgd === 0 ? null : row.amount_sgd > 0 ? 'out' : 'in';

    /** The toast's month line: "September's spending is now S$ 64.00 less." ('' when nothing moved). */
    function spendingWords(change, today) {
        if (!change || !change.minor) return '';
        const [y, m] = change.month.split('-').map(Number);
        const month = LONG_MONTHS[m - 1] + (String(y) === String(today || '').slice(0, 4) ? '' : ` ${y}`);
        return `${month}'s spending is now ${money(Math.abs(change.minor), change.currency)} ${change.minor < 0 ? 'less' : 'more'}.`;
    }

    /** The request the drawer sends (tests/fixtures/expect/mark.json's request). */
    function request(st) {
        const all = st.part === 'all';
        const note = (st.note || '').trim();
        return { direction: st.direction, all, amount: all ? null : String(st.amount || '').trim(),
            names: st.names.slice(), new_names: st.newNames.slice(), note: note || null };
    }

    Expect.mark = { spendingWords, request, shortMoney };

    // The row's pill: what the payload says is expected on it.
    Expect.fill('rowPill', row => {
        const x = row.expected_minor;
        if (!x) return '';
        return ` <span class="xb-pill">${esc(shortMoney(Math.abs(x), row.currency))} ${x < 0 ? 'to pay back' : 'expected back'}</span>`;
    });

    function markButton(row, cls) {
        const way = lane(row);
        if (!way || MOVES.includes(row.flow_type)) return '';
        const words = row.expected_minor
            ? `Change what is ${way === 'in' ? 'to pay back' : 'expected back'}…`
            : way === 'in' ? 'Expect to pay back' : 'Expect some back…';
        return `<button type="button" class="${cls}" data-act="expect-mark" data-tx="${esc(row.id)}">${words}</button>`;
    }
    Expect.fill('rowActions', row => markButton(row, 'btn'));
    // A merchant page lists money out; a refund there is not marked from its row.
    Expect.fill('merchantRowAction', row => lane(row) === 'out' ? markButton(row, 'btn sm ghost') : '');

    // --- the drawer ------------------------------------------------------------------

    const minorText = (minor, cur) => (minor / 10 ** digitsOf(cur)).toFixed(digitsOf(cur));

    function drawerHTML(d, st) {
        const r = d.row, x = d.expectation;
        const ask = st.direction === 'pay' ? 'How much do you expect to pay back?' : 'How much do you expect back?';
        const chosen = new Set(st.names);
        const people = d.people.map(p => `<button type="button" class="chip" data-xb="person" data-id="${p.account_id}" aria-pressed="${chosen.has(p.account_id)}">${esc(p.name)}</button>`).join('')
            + st.newNames.map((n, i) => `<button type="button" class="chip" data-xb="new-person" data-i="${i}" aria-pressed="true" title="New: made when you save. Tap to take it off.">${esc(n)} <span class="n">new</span></button>`).join('');
        const held = x ? `<p class="small muted">${esc(shortMoney(x.set_off_minor, x.currency))} back so far · ${esc(shortMoney(x.open_minor, x.currency))} open</p>` : '';
        return `<div class="xb-target"><b>${esc(r.description)}</b> · ${esc(day(r.date))} · <span class="num">${esc(money(Math.abs(r.amount_minor), r.currency))}</span>${r.display_type ? ` · ${esc(r.display_type)}` : ''} · ${esc(r.book)}</div>
            ${held}
            <div class="xb-field"><span class="xb-lbl">${ask}</span>
                <div class="xb-amount"><div class="seg" role="group" aria-label="How much">
                    <button type="button" data-xb="part" data-k="all" aria-pressed="${st.part === 'all'}">All of it</button>
                    <button type="button" data-xb="part" data-k="part" aria-pressed="${st.part === 'part'}">Part</button></div>
                    ${st.part === 'part' ? `<label class="sr-only" for="xb-amount">Amount</label><input id="xb-amount" class="xb-input xb-input--num num" inputmode="decimal" autocomplete="off" value="${esc(st.amount)}" placeholder="0.00">` : ''}</div></div>
            <p class="xb-yours" id="xb-yours" aria-live="polite"></p>
            <div class="xb-field"><span class="xb-lbl">${st.direction === 'pay' ? 'To whom' : 'From whom'} <span class="xb-opt">(optional, helps fin suggest)</span></span>
                <div class="xb-people">${people}<button type="button" class="btn sm ghost" data-xb="someone-new">+ Someone new</button></div>
                <div class="xb-new" id="xb-new" hidden><label class="sr-only" for="xb-new-name">Their name</label><input id="xb-new-name" class="xb-input xb-input--name" autocomplete="off" placeholder="Their name"><button type="button" class="btn sm" data-xb="add-person">Add</button></div></div>
            <label class="xb-field" for="xb-note"><span class="xb-lbl">Note <span class="xb-opt">(optional)</span></span><input id="xb-note" class="xb-input xb-input--note" autocomplete="off" value="${esc(st.note)}" placeholder="e.g. Kim's birthday dinner"></label>`;
    }

    function redraw(st) {
        closeSheet();
        refreshFrame();
        if (st.fromRow) openRowSheet(st.txId);
        else render();
    }

    async function preview(st, sheet) {
        const ask = ++st.asked;
        const q = new URLSearchParams({ direction: st.direction });
        if (st.part === 'all') q.set('all', '1'); else q.set('amount', st.amount || '');
        const d = await get(`/api/transactions/${st.txId}/expectation?${q}`, { fresh: true });
        if (ask !== st.asked) return;   // a newer keystroke has asked since
        const line = sheet.querySelector('#xb-yours'), save = sheet.querySelector('[data-xb="save"]');
        if (!line) return;
        const w = d.would || {};
        const empty = st.part === 'part' && !(st.amount || '').trim();
        line.classList.toggle('xb-yours--bad', !!w.error && !empty);
        line.textContent = empty ? '' : (w.error || w.yours || '');
        if (save) save.disabled = !!w.error;
    }

    window.openExpectMark = async function openExpectMark(txId, el) {
        const fromRow = !!(el && el.closest && el.closest('#sheets'));
        const d = await get(`/api/transactions/${txId}/expectation`, { fresh: true });
        const x = d.expectation, r = d.row;
        const eyebrow = x ? `Change what is ${what(x.direction)}` : d.direction === 'pay' ? 'Expect to pay back' : 'Expect some back…';
        if (d.refusal && !x) {
            openSheet(esc(r.description), `<p class="notice">${esc(d.refusal)}</p>`, { eyebrow });
            return;
        }
        const left = Math.abs(r.amount_minor) - Math.abs(r.placed_minor);
        const st = { txId, fromRow, asked: 0, direction: d.direction,
            part: x && x.amount_minor === left ? 'all' : 'part',
            amount: x ? minorText(x.amount_minor, x.currency) : '',
            names: x ? x.names.map(n => n.account_id) : [], newNames: [], note: (x && x.note) || '' };
        const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button>`
            + (x ? `<button type="button" class="btn ghost" data-xb="undo-mark">Undo the mark</button>` : '')
            + `<span class="xb-sp"></span><button type="button" class="btn primary" data-xb="save">Save</button>`;
        const sheet = openSheet(esc(r.description), drawerHTML(d, st), { eyebrow, foot });
        const again = () => { sheet.querySelector('.sheet-body').innerHTML = drawerHTML(d, st); preview(st, sheet); };
        let typing = null;

        sheet.addEventListener('input', e => {
            if (e.target.id === 'xb-amount') {
                st.amount = e.target.value;
                clearTimeout(typing);
                typing = setTimeout(() => preview(st, sheet), 180);
            } else if (e.target.id === 'xb-note') st.note = e.target.value;
        });
        const addPerson = () => {
            const input = sheet.querySelector('#xb-new-name'), name = input.value.trim();
            if (!name) { input.focus(); return; }
            const held = d.people.find(p => p.name.toUpperCase() === name.toUpperCase());
            if (held) { if (!st.names.includes(held.account_id)) st.names.push(held.account_id); }
            else if (!st.newNames.some(n => n.toUpperCase() === name.toUpperCase())) st.newNames.push(name);
            again();
        };
        sheet.addEventListener('keydown', e => {
            if (e.target.id === 'xb-new-name' && e.key === 'Enter') { e.preventDefault(); addPerson(); }
        });
        sheet.addEventListener('click', async e => {
            const b = e.target.closest('[data-xb]');
            if (!b) return;
            const k = b.dataset.xb;
            if (k === 'part') {
                st.part = b.dataset.k;
                again();
                if (st.part === 'part') sheet.querySelector('#xb-amount')?.focus();
            } else if (k === 'person') {
                const id = Number(b.dataset.id);
                st.names = st.names.includes(id) ? st.names.filter(n => n !== id) : st.names.concat(id);
                b.setAttribute('aria-pressed', String(st.names.includes(id)));
            } else if (k === 'new-person') { st.newNames.splice(Number(b.dataset.i), 1); again(); }
            else if (k === 'someone-new') { const box = sheet.querySelector('#xb-new'); box.hidden = false; box.querySelector('input').focus(); }
            else if (k === 'add-person') addPerson();
            else if (k === 'save') {
                b.disabled = true;
                const res = await send('POST', `/api/transactions/${txId}/expectation`, request(st));
                if (!res.ok) { b.disabled = false; toast(res.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
                const made = res.data.expectation;
                const said = spendingWords(res.data.spending_change, todayIso())
                    || `${shortMoney(made.amount_minor, made.currency)} ${what(made.direction)} on ${r.description}.`;
                toast(said, { undo: res.change });
                redraw(st);
            } else if (k === 'undo-mark') {
                const res = await send('DELETE', `/api/expectations/${x.id}`);
                if (!res.ok) { toast(res.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
                toast(spendingWords(res.data.spending_change, todayIso()) || 'The mark is undone.', { undo: res.change });
                redraw(st);
            }
        });
        preview(st, sheet);
    };
})();
