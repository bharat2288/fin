// Expect back (personal-ledger 23): Nothing yet, in the Back for… drawer's footer (the backForFoot slot), and attaching a waiting payment.
//
// Filled by personal-ledger 40. Money in may come before the row it is for (Lena's S$ 100 for a
// BBQ not yet charged): Nothing yet leaves what is left of the payment waiting for its row
// (POST /api/transactions/<id>/waiting), named for whom it is from, with a note. It is not
// income; Books lists it under Waiting for its row. When the row comes, Attach… lists the rows
// that may take it (GET /api/set-offs/<id>/attach), each with the split the server says it
// would make, and attaching (POST /api/set-offs/<id>/attach) marks that row expected back and
// sets the waiting money against it at once; a smaller row leaves the rest still waiting.
// Un-wait is Unlink on the waiting set-off (DELETE /api/set-offs/<id>).
//
// Entry points: the footer button opens window.openNothingYet(txId); any element with
// data-xb-waiting="<set-off id>" (Books' Waiting for its row line, personal-ledger 31) opens
// window.openWaitingSetOff(id). Every figure comes from the server's payload; the helpers in
// Expect.waiting only word them. Uses app.js's money, day, esc, get, send, openSheet,
// closeSheet, sheetBody, toast, refreshFrame and rerender at call time.
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};

    // --- pure helpers (tests/test_expect_waiting.py runs them under node) ----------------------

    /** "S$ 120" for a whole amount, "S$ 120.50" otherwise. */
    const short = (minor, cur) => money(minor, cur).replace(/\.00$/, '');
    const escText = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    /** The footer's button for a payment: Nothing yet while something is left to place;
     *  Waiting… when all of it is placed or waiting (the drawer shows what waits); none when
     *  nothing is on it at all. */
    function footButton(p) {
        if (!p) return null;
        if (p.left_minor > 0) return { label: 'Nothing yet', tx: Number(p.id) };
        if (p.placed_minor) return { label: 'Waiting…', tx: Number(p.id) };
        return null;
    }

    /** What attaching to one row would do, in words, from the server's `would`. */
    function wouldWords(w, cur) {
        const head = `Expect back ${short(w.expected_minor, cur)}, closed at once`;
        return w.waiting_minor ? `${head} · ${short(w.waiting_minor, cur)} still waits` : head;
    }

    Expect.waiting = { footButton, wouldWords };

    Expect.fill('backForFoot', p => {
        const b = footButton(p);
        return b ? `<button type="button" class="btn ghost" data-xb-nothing-yet="${b.tx}">${escText(b.label)}</button>` : '';
    });

    // --- the Nothing yet drawer ----------------------------------------------------------------

    function setOffLine(s) {
        const who = s.person ? ` · ${esc(s.person.name)}` : '';
        const note = s.note ? ` · ${esc(s.note)}` : '';
        return `<div class="xb-wl"><span class="xb-wl__t">${esc(short(s.amount_minor, s.currency))} waiting for its row${who}${note}</span>
            <span class="xb-wl__a"><button type="button" class="btn sm" data-xb-attach="${Number(s.id)}">Attach a row…</button>
            <button type="button" class="btn sm ghost" data-xb-unwait="${Number(s.id)}">Un-wait</button></span></div>`;
    }

    function nothingYetHTML(d, st) {
        const p = d.payment;
        const error = st.error ? `<p class="notice bad">${esc(st.error)}</p>` : '';
        const lines = d.waiting.length ? `<div class="xb-wlist">${d.waiting.map(setOffLine).join('')}</div>` : '';
        if (p.left_minor <= 0) {
            return `${error}${lines
                || `<p class="muted small">${esc(short(0, p.currency))} left on this payment, and nothing on it waits for its row.</p>`}`;
        }
        const people = d.people.map(x => `<button type="button" class="chip" data-xb-who="${x.account_id}" aria-pressed="${st.person === x.account_id}">${esc(x.name)}</button>`).join('');
        return `${error}${lines}
            <p class="xb-q">${esc(short(p.left_minor, p.currency))} waits for its row. It is not income.</p>
            <div class="xb-field"><span class="xb-lbl">From whom <span class="xb-opt">(optional)</span></span>
                <div class="xb-people">${people}</div></div>
            <label class="xb-field" for="xb-wait-note"><span class="xb-lbl">Note <span class="xb-opt">(optional)</span></span>
                <input id="xb-wait-note" class="xb-input xb-input--note" autocomplete="off" value="${esc(st.note)}" placeholder="e.g. for the BBQ, when it is charged"></label>`;
    }

    function done(words, change) {
        closeSheet();
        toast(words, { undo: change });
        refreshFrame();
        rerender();
    }

    window.openNothingYet = async function openNothingYet(txId) {
        let d;
        try { d = await get(`/api/transactions/${txId}/waiting`, { fresh: true }); }
        catch (err) { openSheet('A payment', `<p class="notice bad">${esc(err.message)}</p>`, { eyebrow: 'Nothing yet' }); return; }
        const st = { person: d.sender ? d.sender.account_id : null, note: '', error: null };
        const p = d.payment;
        const foot = `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button><span class="xb-sp"></span>`
            + (p.left_minor > 0 ? `<button type="button" class="btn primary" data-xb-wait>Leave it waiting</button>` : '');
        const sheet = openSheet(esc(p.description), nothingYetHTML(d, st), {
            eyebrow: 'Nothing yet', sub: `${esc(day(p.date))} · ${esc(money(Math.abs(p.amount_minor), p.currency))}`, foot });
        sheet.addEventListener('input', e => { if (e.target.id === 'xb-wait-note') st.note = e.target.value; });
        sheet.addEventListener('click', async e => {
            const who = e.target.closest('[data-xb-who]');
            if (who) {
                const id = Number(who.dataset.xbWho);
                st.person = st.person === id ? null : id;
                sheetBody().innerHTML = nothingYetHTML(d, st);
                return;
            }
            if (!e.target.closest('[data-xb-wait]')) return;
            e.preventDefault();
            const r = await send('POST', `/api/transactions/${txId}/waiting`, { person_id: st.person, note: st.note || null });
            if (!r.ok) { st.error = r.data.error || 'That did not work; nothing was changed'; sheetBody().innerHTML = nothingYetHTML(d, st); return; }
            const s = r.data.set_off;
            done(`${short(s.amount_minor, s.currency)} waits for its row${s.person ? ` (${s.person.name})` : ''}. It is not income.`, r.change);
        });
    };

    // --- attaching a waiting set-off, and Un-wait ----------------------------------------------

    function attachHTML(d, error) {
        const s = d.set_off;
        if (s.state !== 'waiting') return `<p class="notice">This payment is placed already; it is not waiting for its row.</p>`;
        const rows = d.rows.map(r => `<button type="button" class="xb-al xb-wrow" data-xb-row="${Number(r.id)}">
                <span class="xb-al__t">${esc(r.description)} · ${esc(day(r.date, { year: false }))}</span>
                <span class="xb-al__in"><span class="num">${esc(money(Math.abs(r.amount_minor), r.currency))}</span></span>
                <span class="xb-al__m">${esc(wouldWords(r.would, r.currency))}${r.display_type ? ` · ${esc(r.display_type)}` : ''}</span></button>`).join('');
        return `${error ? `<p class="notice bad">${esc(error)}</p>` : ''}
            <p class="xb-q">Which row is it for?</p>
            ${rows ? `<div class="xb-alloc" role="group" aria-label="Rows">${rows}</div>`
                : `<p class="muted small">No row in the 90 days after the payment can take it yet. It keeps waiting; nothing happens by itself.</p>`}
            <p class="xb-learn"><button type="button" class="link" data-xb-unwait="${Number(s.id)}">Un-wait: give the payment its ${esc(short(s.amount_minor, s.currency))} back to label</button></p>`;
    }

    async function unwait(id) {
        const r = await send('DELETE', `/api/set-offs/${id}`);
        if (!r.ok) { toast(r.data.error || 'That did not work; nothing was changed'); return; }
        const p = r.data.payment;
        done(`${short(p.left_minor, p.currency)} is back on the payment, to place or label.`, r.change);
    }

    window.openWaitingSetOff = async function openWaitingSetOff(id) {
        let d;
        try { d = await get(`/api/set-offs/${id}/attach`, { fresh: true }); }
        catch (err) { openSheet('Waiting for its row', `<p class="notice bad">${esc(err.message)}</p>`, { eyebrow: 'Attach' }); return; }
        const s = d.set_off;
        const sub = `${esc(day(s.date))} · ${esc(short(s.amount_minor, s.currency))}${s.person ? ` · ${esc(s.person.name)}` : ''}${s.note ? ` · ${esc(s.note)}` : ''}`;
        const sheet = openSheet('Waiting for its row', attachHTML(d, null), {
            eyebrow: 'Attach', sub, foot: `<button type="button" class="btn ghost" data-act="close-sheet">Cancel</button>` });
        sheet.addEventListener('click', async e => {
            const pick = e.target.closest('[data-xb-row]');
            if (!pick) return;
            e.preventDefault();
            const r = await send('POST', `/api/set-offs/${id}/attach`, { row_id: Number(pick.dataset.xbRow) });
            if (!r.ok) { sheetBody().innerHTML = attachHTML(d, r.data.error || 'That did not work; nothing was changed'); return; }
            const x = r.data.expectation, w = r.data.waiting;
            done(`${x.row.description}: ${short(x.amount_minor, x.currency)} expected back, closed at once${w ? `; ${short(w.amount_minor, w.currency)} still waits` : ''}.`, r.change);
        });
    };

    // One listener for the entry points, wherever they are drawn (the Back for… footer, the
    // Nothing yet drawer's lines, Books' Waiting for its row).
    // (No document under node, where tests/ load these shells for their pure parts.)
    if (typeof document !== 'undefined') document.addEventListener('click', e => {
        const t = e.target.closest && e.target.closest('[data-xb-nothing-yet], [data-xb-attach], [data-xb-waiting], [data-xb-unwait]');
        if (!t || t.disabled) return;
        e.preventDefault();
        if (t.dataset.xbNothingYet) { closeSheet(); window.openNothingYet(Number(t.dataset.xbNothingYet)); }
        else if (t.dataset.xbUnwait) unwait(Number(t.dataset.xbUnwait));
        else { closeSheet(); window.openWaitingSetOff(Number(t.dataset.xbAttach || t.dataset.xbWaiting)); }
    });
})();
