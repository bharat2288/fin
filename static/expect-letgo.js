// Expect back (personal-ledger 23): the Let it go confirm (D4): window.openLetGo, window.undoLetGo.
//
// Personal-ledger 37 fills this shell. A centred confirm on one expectation's open rest:
//   a back item (expected back): "It was mine after all" (its row's type and book; a type is
//     asked for when the row has none) or "A gift" (Gifts & Donations · Household, to whom);
//   a pay item (to pay back): only "A gift to me", with an income type.
// Let it go stays disabled until a choice, and any type it asks for, is picked. The server takes
// the whole open rest and counts it today; every figure here is the payload's (the expectation's
// page, GET /api/expectations/<id>, and the let-go route's answer), never a formula.
//
// Entry points: ACT['let-go'] (app.js) calls window.openLetGo(expectationId); a let-go line's
// Undo is a button with data-act="undo-let-go" data-id="<expectation id>".
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};
    const BACK_Q = 'What was it, now that it is not coming back?';
    const PAY_Q = 'What was it, now that it is not being paid back?';
    const GIFTS = 'Gifts & Donations';

    /** "S$ 20" for a whole amount, "S$ 20.50" otherwise: the payload's figure, in words. */
    function amount(minor, cur) { return money(minor, cur).replace(/\.00$/, ''); }
    function itemTitle(e) {
        if (e.row) return e.row.description;
        return `Opening figure · ${(e.opening && e.opening.name) || (e.names[0] || {}).name || ''}`;
    }

    const L = Expect.letGo = {
        /** Today, as the server counts it: the viewer's own day. */
        today() {
            const d = new Date();
            return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        },

        /** The choices a page's expectation offers, by direction. asks_type: the kind of type the
         *  choice needs picked first ('spending' | 'income'), or null when it asks for none. */
        choices(page) {
            const e = page.expectation;
            if (e.direction === 'pay') {
                return [{ k: 'gift', title: 'A gift to me', sub: 'Income · Household', asks_type: 'income' }];
            }
            const row = e.row;
            const book = (row && row.book) || 'Household';
            const typed = !!row && row.type_id !== null && row.type_id !== undefined;
            return [
                { k: 'mine', title: 'It was mine after all',
                  sub: typed ? `Spending: ${row.display_type} · ${book}` : `Spending: pick its type · ${book}`,
                  asks_type: typed ? null : 'spending' },
                { k: 'gift', title: 'A gift', sub: `Spending: ${GIFTS} · Household`, asks_type: null },
            ];
        },

        /** The route's request for what is picked ({pick, to, type}), or null while Let it go
         *  stays disabled: nothing picked, or a type asked for and not yet picked. */
        request(page, { pick = null, to = null, type = null } = {}) {
            const choice = L.choices(page).find(c => c.k === pick);
            if (!choice) return null;
            const typeId = type === null || type === undefined || type === '' ? null : Number(type);
            if (choice.asks_type && typeId === null) return null;
            const gift = pick === 'gift' && page.expectation.direction === 'back';
            const toId = gift && to !== null && to !== undefined && to !== '' ? Number(to) : null;
            return { became: pick, to_id: toId, type_id: choice.asks_type ? typeId : null };
        },

        /** A let-go in words, from the route's let_go: "S$ 20 let go as a gift to Kim". */
        words(lg, direction) {
            const what = amount(lg.amount_minor, lg.currency);
            if (direction === 'pay') return `${what} let go as a gift to me, ${lg.display_type}`;
            if (lg.became === 'gift') return `${what} let go as a gift${lg.to ? ` to ${lg.to.name}` : ''}`;
            return `${what} let go as mine, ${lg.display_type} · ${lg.book}`;
        },
    };

    // The confirm's state while it is open: the page, what is picked, the types it may ask for.
    let st = null;

    function bodyHTML() {
        const e = st.page.expectation;
        const choices = L.choices(st.page);
        const picked = choices.find(c => c.k === st.pick);
        const tiles = choices.map(c => `<button type="button" class="choice" data-act="xb-lg-pick" data-k="${c.k}" aria-pressed="${st.pick === c.k}">
            <strong>${esc(c.title)}</strong><span>${esc(c.sub)}</span></button>`).join('');
        const to = st.pick === 'gift' && e.direction === 'back' && e.names.length
            ? `<div class="xb-lg-to" role="group" aria-label="To whom"><span class="xb-lg-label">To whom</span>
                ${e.names.map(n => `<button type="button" class="btn sm" data-act="xb-lg-to" data-to="${n.account_id}" aria-pressed="${String(st.to) === String(n.account_id)}">${esc(n.name)}</button>`).join('')}
                <button type="button" class="btn sm ghost" data-act="xb-lg-to" data-to="" aria-pressed="${st.to === null}">No one</button></div>`
            : '';
        const kind = picked && picked.asks_type;
        const label = kind === 'income' ? 'Income type' : 'Its type';
        const type = kind
            ? `<label class="field xb-lg-type"><span>${label}</span>
                <select class="sel" data-change="xb-lg-type" aria-label="${label}"><option value="">Choose a type</option>
                ${(st.types[kind] || []).map(t => `<option value="${t.id}"${String(t.id) === String(st.type) ? ' selected' : ''}>${esc(t.display_name || t.name)}</option>`).join('')}
                </select></label>`
            : '';
        const today = L.today();
        const month = LONG_MONTHS[Number(today.slice(5, 7)) - 1];
        return `<p class="xb-lg-q">${e.direction === 'pay' ? PAY_Q : BACK_Q}</p>
            <div class="choices">${tiles}</div>${to}${type}
            <p class="xb-lg-note">Counted on <b>${esc(day(today, { year: false }))}</b>, today, so ${esc(month)} as you read it does not change. It shows in Changes and can be undone.</p>`;
    }
    function footHTML() {
        const ready = L.request(st.page, st) !== null;
        return `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button><span class="xb-lg-sp"></span>
            <button type="button" class="btn primary" data-act="xb-lg-go"${ready ? '' : ' disabled'}>Let it go</button>`;
    }
    function drawFoot() { const foot = $('#sheets .sheet-foot'); if (foot) foot.innerHTML = footHTML(); }
    function draw() { const body = sheetBody(); if (body) body.innerHTML = bodyHTML(); drawFoot(); }

    window.openLetGo = async function openLetGo(expectationId) {
        let page;
        try { page = await get(`/api/expectations/${expectationId}`, { fresh: true }); }
        catch (err) { toast(err.message || 'fin could not read this expectation', { bad: true }); return; }
        const e = page.expectation;
        const kinds = new Set(L.choices(page).map(c => c.asks_type).filter(Boolean));
        const types = {};
        try {
            if (kinds.has('spending')) types.spending = await get('/api/types');
            if (kinds.has('income')) types.income = await get('/api/types?kind=income');
        } catch (err) { toast(err.message || 'fin could not read the types', { bad: true }); return; }
        // A gift goes to the one person still out, when the page names one.
        const open = (page.lines || []).find(l => l.kind === 'open');
        const named = open && open.name ? e.names.find(n => n.name === open.name) : null;
        st = { id: expectationId, page, pick: null, to: named ? named.account_id : null, type: null, types };
        openSheet(`${esc(money(e.open_minor, e.currency))} from ${esc(itemTitle(e))}`, bodyHTML(),
            { eyebrow: 'Let it go', center: true, foot: footHTML() });
    };

    // app.js's action table; absent only where a harness loads the shells without app.js.
    const acts = typeof ACT === 'undefined' ? {} : ACT;
    acts['xb-lg-pick'] = el => { if (!st) return; st.pick = el.dataset.k; st.type = null; draw(); };
    acts['xb-lg-to'] = el => { if (!st) return; st.to = el.dataset.to === '' ? null : Number(el.dataset.to); draw(); };
    acts['xb-lg-type'] = el => { if (!st) return; st.type = el.value === '' ? null : el.value; drawFoot(); };
    acts['xb-lg-go'] = async () => {
        if (!st) return;
        const req = L.request(st.page, st);
        if (!req) return;
        const { id, page } = st;
        const r = await send('POST', `/api/expectations/${id}/let-go`, req);
        if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
        st = null;
        closeSheet();
        const lg = r.data.let_go;
        toast(`${L.words(lg, page.expectation.direction)}, counted on ${day(lg.counts_on, { year: false })}.`, { undo: r.change });
        refreshFrame();
        if (typeof rerender === 'function') rerender();
    };

    /** Undo the let-go: its open rest is open again. */
    window.undoLetGo = async function undoLetGo(expectationId) {
        const r = await send('DELETE', `/api/expectations/${expectationId}/let-go`);
        if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); return; }
        const e = r.data.expectation;
        toast(`${amount(e.open_minor, e.currency)} open again.`, { undo: r.change });
        refreshFrame();
        if (typeof rerender === 'function') rerender();
    };
    acts['undo-let-go'] = el => window.undoLetGo(Number(el.dataset.id));
})();
