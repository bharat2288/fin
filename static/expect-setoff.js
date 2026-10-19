// Expect back (personal-ledger 23): the Back for… drawer (D2): window.openBackFor, Expect.draw('backForFoot', payment) in its footer.
//
// Filled by personal-ledger 36. On a money-in row (or a movement out, against To pay back) the
// row sheet offers Back for…; the drawer lists the open items nearest the payment first
// (GET /api/transactions/<id>/back-for), ticks fill oldest first, each amount stays editable,
// a running line says what is placed, and Set off sends every placement in one request
// (POST /api/transactions/<id>/set-offs), written all or none as one change. What is not placed
// stays on the payment, to label. Every figure comes from the server's payload; the helpers in
// Expect.setoff only add up what the operator typed, and the server checks it all again.
//
// Uses app.js's money, day, esc, get, send, openSheet, closeSheet, sheetBody, toast,
// refreshFrame and rerender at call time (index.html loads this after app.js).
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};

    // --- pure helpers (tests/test_fill_oldest_first.py runs them under node) -----------------

    /** "S$ 120" for a whole amount, "S$ 120.50" otherwise. */
    const short = (minor, cur) => money(minor, cur).replace(/\.00$/, '');

    /** Ticks fill oldest first: each ticked item, oldest day first, takes what the payment has
     *  left, capped by what is open on it. An item kept in another currency is not filled here
     *  (the rate step, ticket 39, converts). Returns {id: minor} for every ticked item. */
    function fillOldestFirst(items, leftMinor, currency) {
        const out = {};
        let left = Math.max(0, leftMinor);
        const order = items.slice().sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : a.id - b.id));
        for (const it of order) {
            if (it.currency !== currency) { out[it.id] = 0; continue; }
            const take = Math.min(it.open_minor, left);
            out[it.id] = take;
            left -= take;
        }
        return out;
    }

    /** The running line under the list: what is placed of what the payment has left, and what
     *  stays on it or goes over. can: whether Set off may be pressed. */
    function placedLine(placedMinor, leftMinor, currency) {
        if (leftMinor <= 0) return { text: `${short(0, currency)} left on this payment`, over: placedMinor > 0, can: false };
        const head = `${short(placedMinor, currency)} of ${short(leftMinor, currency)} placed`;
        if (placedMinor > leftMinor) return { text: `${head} · ${short(placedMinor - leftMinor, currency)} more than the payment`, over: true, can: false };
        if (placedMinor < leftMinor) return { text: `${head} · ${short(leftMinor - placedMinor, currency)} stays on this payment, to label`, over: false, can: placedMinor > 0 };
        return { text: head, over: false, can: true };
    }

    /** "Expect to pay this back instead…": only on money in, and only once nothing is ticked
     *  (ticks are drawer state, never saved, so clearing them undoes nothing). */
    function insteadLink(tickedCount, paymentAmountMinor) {
        if (!(paymentAmountMinor < 0)) return null;
        return tickedCount ? { disabled: true, hint: 'Clear the ticks first' } : { disabled: false, hint: '' };
    }

    /** What an item is called in the list: its row's text; a converted loan by whom it was to
     *  or from; an opening figure by its person. */
    function itemTitle(e) {
        if (e.opening) return `Opening figure · ${e.opening.person.name}`;
        if (e.source === 'converted' && e.names && e.names.length === 1) {
            return `${e.direction === 'pay' ? 'Loan from' : 'Loan to'} ${e.names[0].name}`;
        }
        return e.row ? e.row.description : '';
    }

    /** "S$ 40 of S$ 620 open", or "S$ 200 open" while nothing has come back. */
    function openWords(e) {
        return e.open_minor < e.amount_minor
            ? `${short(e.open_minor, e.currency)} of ${short(e.amount_minor, e.currency)} open`
            : `${short(e.open_minor, e.currency)} open`;
    }

    /** The toast once a set-off is written. direction is the items': 'pay' items are paid back. */
    function doneToast(resp, titles, direction) {
        const offs = resp.set_offs || [];
        const total = offs.reduce((s, o) => s + o.amount_minor, 0);
        const cur = offs.length ? offs[0].currency : 'SGD';
        if (direction === 'pay') return `${short(total, cur)} paid back on ${day(offs[0].date, { year: false })}`;
        const list = titles.length > 1 ? `${titles.slice(0, -1).join(', ')} and ${titles[titles.length - 1]}` : titles[0];
        const said = `Set off ${short(total, cur)} against ${list}.`;
        const l = resp.learnt;
        return l ? `${said} Next time '${l.wording}' suggests ${l.person.name} first.` : said;
    }

    /** An amount as typed, in whole minor units, or null when it is not one (the server reads
     *  the text again with money.parse_minor). */
    function parseAmount(text, currency) {
        const digits = digitsOf(currency);
        const t = String(text ?? '').trim().replace(/,/g, '');
        const m = t.match(/^(\d+)(?:\.(\d*))?$/);
        if (!m || (m[2] || '').length > digits) return null;
        return Number(m[1]) * 10 ** digits + Number((m[2] || '').padEnd(digits, '0') || 0);
    }

    /** Whole minor units as the decimal text a request carries: 12000 -> "120.00". */
    function amountText(minor, currency) {
        const digits = digitsOf(currency);
        if (!digits) return String(minor);
        const unit = 10 ** digits;
        return `${Math.floor(minor / unit)}.${String(minor % unit).padStart(digits, '0')}`;
    }

    Expect.setoff = { fillOldestFirst, placedLine, insteadLink, itemTitle, openWords, doneToast, parseAmount, amountText };

    // --- the row sheet's action ----------------------------------------------------------------

    // Money in may be back for something; money out only as a movement (to a person, against
    // what is to pay back). Moves between own accounts never pay anything.
    Expect.fill('rowActions', row => {
        const amount = Number(row.amount_sgd);
        const offered = (amount < 0 && !['transfer', 'payment'].includes(row.flow_type))
            || (amount > 0 && row.flow_type === 'movement');
        return offered ? `<button class="btn" data-act="back-for" data-tx="${Number(row.id)}">Back for…</button>` : '';
    });

    // --- the drawer ----------------------------------------------------------------------------

    let st = null;   // the open drawer: {txId, data, tick: Set, amt: Map, typed: Map, error}

    const escapeRe = s => s.replace(/[.*+?^$()|[\]\\{}]/g, '\\$&');
    /** Whether a name on an item is the payment's sender, as the server reads it: a learnt
     *  wording, or the name as a word in the payment's text. Only for bolding the name. */
    function payerNames(data) {
        const text = String(data.payment.description || '').toUpperCase();
        const said = data.payment.sender && data.payment.sender.person;
        return name => (said && said.name === name)
            || new RegExp('(^|[^A-Z0-9])' + escapeRe(name.toUpperCase()) + '([^A-Z0-9]|$)').test(text);
    }

    function itemsById() { return new Map(st.data.items.map(e => [e.id, e])); }
    function placedMinor() {
        let total = 0;
        for (const id of st.tick) total += st.amt.get(id) ?? 0;
        return total;
    }
    function anyBad() { for (const id of st.tick) if (st.amt.get(id) === null) return true; return false; }

    function lineHTML() {
        const p = st.data.payment;
        const line = placedLine(placedMinor(), p.left_minor, p.currency);
        const bad = anyBad();
        return { html: `<div class="xb-placed${line.over ? ' xb-over' : ''}" data-xb-placed>${esc(bad ? 'An amount is not in whole cents' : line.text)}</div>`, can: line.can && !bad };
    }

    function bodyHTML() {
        const { payment: p, items, direction } = st.data;
        const isPayer = payerNames(st.data);
        const list = items.map(e => {
            const on = st.tick.has(e.id);
            const title = itemTitle(e);
            const names = (e.names || []).map(n => isPayer(n.name) ? `<b>${esc(n.name)}</b>` : esc(n.name)).join(', ');
            const foreign = e.currency !== p.currency ? ` · kept in ${esc(e.currency)}` : '';
            const amount = on
                ? `<input class="xb-amt" inputmode="decimal" aria-label="Amount for ${esc(title)}" data-xb-amt="${e.id}" value="${esc(st.typed.get(e.id) ?? '')}">`
                : `<span class="xb-pill">${esc(short(e.open_minor, e.currency))} open</span>`;
            return `<label class="xb-al${on ? ' xb-on' : ''}"><input type="checkbox" data-xb-tick="${e.id}"${on ? ' checked' : ''}>
                <span class="xb-al__t">${esc(title)} · ${esc(day(e.day, { year: false }))}</span>
                <span class="xb-al__in">${amount}</span>
                <span class="xb-al__m">${esc(openWords(e))}${names ? ` · ${names}` : ''}${foreign}</span></label>`;
        }).join('');
        const instead = insteadLink(st.tick.size, p.amount_minor);
        const what = direction === 'pay' ? 'What is it paying back?' : 'What is it back for?';
        return `${st.error ? `<p class="notice bad">${esc(st.error)}</p>` : ''}
            <p class="xb-q">${what}</p>
            ${items.length ? `<div class="xb-alloc" role="group" aria-label="Open items">${list}</div>`
                : `<p class="muted small">Nothing is open ${direction === 'pay' ? 'to pay back' : 'to come back'} to set this payment against.</p>`}
            ${lineHTML().html}
            ${instead ? `<p class="xb-learn"><button type="button" class="link xb-instead" data-xb-instead${instead.disabled ? ' disabled aria-disabled="true"' : ''}>Expect to pay this back instead…</button>${instead.hint ? ` <span class="muted">${esc(instead.hint)}</span>` : ''}</p>` : ''}`;
    }

    function setOffLabel() {
        const p = st.data.payment;
        return `Set off ${short(Math.min(placedMinor(), p.left_minor), p.currency)}`;
    }

    function redrawLine() {
        const sheet = document.querySelector('#sheets .sheet');
        if (!sheet || !st) return;
        const line = lineHTML();
        const box = sheet.querySelector('[data-xb-placed]');
        if (box) box.outerHTML = line.html;
        const btn = sheet.querySelector('[data-xb-setoff]');
        if (btn) { btn.disabled = !line.can; btn.textContent = setOffLabel(); }
    }

    function redraw() {
        sheetBody().innerHTML = bodyHTML();
        redrawLine();
    }

    function onTick(id, on) {
        if (on) st.tick.add(id); else st.tick.delete(id);
        const byId = itemsById();
        const p = st.data.payment;
        const filled = fillOldestFirst([...st.tick].map(i => byId.get(i)), p.left_minor, p.currency);
        st.amt = new Map(Object.entries(filled).map(([k, v]) => [Number(k), v]));
        st.typed = new Map([...st.amt].map(([k, v]) => [k, amountText(v, p.currency)]));
        redraw();
    }

    function onAmount(id, text) {
        st.typed.set(id, text);
        st.amt.set(id, parseAmount(text, st.data.payment.currency));
        redrawLine();
    }

    async function onSetOff() {
        const { payment: p, direction } = st.data;
        const byId = itemsById();
        const chosen = [...st.tick].filter(id => (st.amt.get(id) ?? 0) > 0);
        const placements = chosen.map(id => ({
            expectation_id: id, amount: amountText(st.amt.get(id), p.currency), open_minor: byId.get(id).open_minor }));
        const r = await send('POST', `/api/transactions/${st.txId}/set-offs`, { placements, waiting: null });
        if (!r.ok) {
            st.error = r.data.error || 'That did not work; nothing was changed';
            redraw();
            return;
        }
        const words = doneToast(r.data, chosen.map(id => itemTitle(byId.get(id))), direction);
        st = null;
        closeSheet();
        toast(words, { undo: r.change });
        refreshFrame();
        rerender();
    }

    window.openBackFor = async function openBackFor(txId) {
        openSheet('A payment', '<p class="loading">Loading…</p>', { eyebrow: 'Back for…' });
        let data;
        try { data = await get(`/api/transactions/${txId}/back-for`, { fresh: true }); }
        catch (err) { sheetBody().innerHTML = `<p class="notice bad">${esc(err.message)}</p>`; return; }
        st = { txId, data, tick: new Set(), amt: new Map(), typed: new Map(), error: null };
        const p = data.payment;
        const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button>${Expect.draw('backForFoot', p)}<span class="xb-sp"></span>
            <button type="button" class="btn primary" data-xb-setoff disabled>${esc(setOffLabel())}</button>`;
        const sheet = openSheet(esc(p.description), bodyHTML(), {
            eyebrow: 'Back for…', sub: `${esc(day(p.date))} · ${esc(money(Math.abs(p.amount_minor), p.currency))}`, foot });
        redrawLine();
        sheet.addEventListener('change', e => {
            const t = e.target;
            if (t.dataset.xbTick) onTick(Number(t.dataset.xbTick), t.checked);
        });
        sheet.addEventListener('input', e => {
            const t = e.target;
            if (t.dataset.xbAmt) onAmount(Number(t.dataset.xbAmt), t.value);
        });
        sheet.addEventListener('click', e => {
            const t = e.target.closest('[data-xb-setoff], [data-xb-instead]');
            if (!t || t.disabled) return;
            e.preventDefault();
            if (t.hasAttribute('data-xb-setoff')) onSetOff();
            else { st = null; closeSheet(); window.openExpectMark?.(txId); }
        });
    };
})();
