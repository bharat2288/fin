// Expect back (personal-ledger 23): the Queue's Back for… step on Money in (D6), in the queueStep slot.
//
// Filled by personal-ledger 41 and 42. First in the Money in lane's body, a "Back for…" step draws:
//   - each money-in row the lane read (GET /api/queue/paybacks) found a match for: one best match
//     with the server's reason chips, ✓ Set off, Another… (the Back for… drawer) and Not a payback;
//     two or more that tie, a ✓ for each;
//   - each payment left waiting for its row that the lane read found rows for (personal-ledger
//     42): "Expect back S$ 100 and set Lena's S$ 100 against it?" with a ✓ per row, nearest
//     first as the server ranked them, and Another… (personal-ledger 40's attach-by-hand drawer);
//   - each refund two purchases fit (GET /api/refund-links/asks, personal-ledger 38's read):
//     "Refund of which … order?" with a ✓ per purchase that writes the hand link; and the
//     "keep it, or To pay back?" ask (personal-ledger 43's, drawn from its fixture shape).
// A money-in row that fits nothing stays a plain row in its own card below, and gets Back for…
// beside its This was…. Nothing links without a tap.
//
// ✓ Set off posts Back for…'s own route (POST /api/transactions/<id>/set-offs), the hand link
// refund_link_api's (POST /api/refund-links), a waiting offer's ✓ the attach route
// (POST /api/set-offs/<id>/attach); Not a payback POSTs /api/transactions/<id>/not-a-payback.
// Each is one change, undone from the toast or from Changes. Every figure and every reason comes
// from the server's payload (tests/fixtures/expect/), never a formula here.
//
// Uses app.js's money, day, esc, get, send, act, toast, refreshFrame and rerender at call time
// (index.html loads this after app.js); read alone, it draws nothing.
(function () {
    'use strict';

    const Expect = window.Expect = window.Expect || {};

    // --- pure helpers (tests/test_payback_queue.py runs them under node) ----------------------

    /** "S$ 120" for a whole amount, "S$ 120.50" otherwise. */
    const short = (minor, cur) => money(minor, cur).replace(/\.00$/, '');
    const shortDay = iso => day(iso, { year: false });
    const COUNTS = ['', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten'];

    /** The request ✓ Set off sends for one suggestion: what the payment has left, all on that
     *  item (the server offered it because its open amount is at least that), with the open
     *  amount the Queue showed, so a change made since is named as such. */
    function setOffRequest(payback, suggestion) {
        const p = payback.payment;
        return {
            placements: [{ expectation_id: suggestion.expectation_id,
                amount: Expect.setoff.amountText(p.left_minor, p.currency), open_minor: suggestion.open_minor }],
            waiting: null,
        };
    }

    /** The request a waiting offer's ✓ sends: attach the waiting set-off to that row. */
    function attachRequest(offer, r) {
        return { row_id: Number(r.id) };
    }

    /** The hand link a refund ask's ✓ writes. */
    function handLinkRequest(ask, purchase) {
        return { refund_row_id: ask.refund.id, purchase_row_id: purchase.id };
    }

    /** The answer to "keep it, or To pay back?" (personal-ledger 43's route). */
    function answerRequest(answer) {
        return { answer };
    }

    /** The merchant as a person says it: "AMAZON.SG" -> "Amazon". Words only, no figure. */
    function merchantWord(description) {
        const first = String(description || '').trim().split(/[.\s*/]+/)[0] || '';
        return first ? first.charAt(0).toUpperCase() + first.slice(1).toLowerCase() : 'this';
    }

    /** The ids of the payments the lane read has no suggestion for: plain rows, which get
     *  Back for… beside their This was…. */
    function plainIds(lane) {
        return (lane.paybacks || []).filter(p => !p.suggestions.length).map(p => p.payment.id);
    }

    function backForButton(txId) {
        return `<button type="button" class="btn sm ghost" data-act="back-for" data-tx="${Number(txId)}" data-xb-back-beside>Back for…</button>`;
    }

    /** One line of the step: what | amount | (nothing), with the ask below it. */
    function itemHTML(title, meta, amount, ask) {
        return `<article class="qi xb-q">
            <div class="qi__w"><p class="qi__t">${esc(title)}</p><p class="qi__m">${esc(meta)}</p></div>
            <div class="qi__amt num">${esc(amount)}</div>
            <div class="qi__act"></div>
            <div class="qi__more">${ask}</div></article>`;
    }

    function reasonsHTML(reasons) {
        return `<div class="xb-q-why">${reasons.map(r => `<span>${esc(r)}</span>`).join('')}</div>`;
    }

    function paybackHTML(pb) {
        const p = pb.payment, tx = Number(p.id), s = pb.suggestions;
        const another = `<button type="button" class="btn sm" data-act="back-for" data-tx="${tx}">Another…</button>`;
        const not = `<button type="button" class="btn sm ghost" data-act="xb-q-not" data-tx="${tx}">Not a payback</button>`;
        const tick = (x, words) => `<button type="button" class="btn sm subtle" data-act="xb-q-setoff" data-tx="${tx}" data-x="${Number(x.expectation_id)}" title="${esc(x.reasons.join(' · '))}">${words}</button>`;
        let ask;
        if (s.length === 1) {
            const x = s[0];
            ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Back for <b>${esc(x.title)}, ${esc(shortDay(x.date))}</b>? <span class="xb-q-open">${esc(short(x.open_minor, x.currency))} open</span></p>
                ${reasonsHTML(x.reasons)}
                <div class="xb-q-acts">${tick(x, '✓ Set off')}${another}${not}</div></div>`;
        } else {
            const n = COUNTS[s.length] || String(s.length);
            ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Back for one of these? ${n} fit equally:</p>
                <div class="xb-q-acts">${s.map(x => tick(x, `✓ ${esc(x.title)}, ${esc(shortDay(x.date))} · ${esc(short(x.open_minor, x.currency))} open`)).join('')}</div>
                <div class="xb-q-acts">${another}${not}</div></div>`;
        }
        return itemHTML(p.description, `${shortDay(p.date)} · ${p.account_name || ''}`, short(p.left_minor, p.currency), ask);
    }

    /** One waiting set-off with the rows that have come in for it. The server offers only
     *  rows with room for the whole waiting amount, so the expectation is that amount. */
    function waitingHTML(w) {
        const p = w.payment, id = Number(w.set_off_id), amt = short(w.amount_minor, w.currency);
        const whose = w.person ? `${esc(w.person.name)}'s ${esc(amt)}` : `the ${esc(amt)} waiting`;
        const question = `<p class="xb-q-sug__t">Expect back ${esc(amt)} and set ${whose} against it?</p>`;
        const rowWords = r => `${esc(r.description)}, ${esc(shortDay(r.date))} · ${esc(short(r.amount_minor, r.currency))}`;
        const tick = (r, words) => `<button type="button" class="btn sm subtle" data-act="xb-q-attach" data-so="${id}" data-row="${Number(r.id)}">${words}</button>`;
        const another = `<button type="button" class="btn sm" data-xb-waiting="${id}">Another…</button>`;
        let ask;
        if (w.rows.length === 1) {
            const r = w.rows[0];
            ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Its row: <b>${rowWords(r)}</b>?</p>${question}
                <div class="xb-q-acts">${tick(r, `✓ Set it against ${esc(r.description)}`)}${another}</div></div>`;
        } else {
            const n = COUNTS[w.rows.length] || String(w.rows.length);
            ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Its row, one of these? ${n} fit:</p>${question}
                <div class="xb-q-acts">${w.rows.map(r => tick(r, `✓ ${rowWords(r)}`)).join('')}</div>
                <div class="xb-q-acts">${another}</div></div>`;
        }
        const who = w.person ? ` · ${w.person.name}` : '';
        return itemHTML(p.description, `${shortDay(p.date)} · ${p.account_name || ''} · waiting for its row${who}`, amt, ask);
    }

    function whichHTML(a) {
        const r = a.refund;
        const ticks = a.purchases.map(p => `<button type="button" class="btn sm subtle" data-act="xb-q-refund" data-refund="${Number(r.id)}" data-purchase="${Number(p.id)}" data-day="${esc(shortDay(p.date))}">✓ ${esc(shortDay(p.date))} · ${esc(short(p.amount_minor, p.currency))}</button>`).join('');
        const ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Refund of which ${esc(merchantWord(a.purchases[0] && a.purchases[0].description))} order?</p>
            <div class="xb-q-acts">${ticks}</div></div>`;
        return itemHTML(r.description, `${shortDay(r.date)} · ${r.account_name || ''}`, short(Math.abs(r.amount_minor), r.currency), ask);
    }

    function paidBackHTML(a) {
        const r = a.refund, pu = a.purchase;
        const names = (a.names || []).map(n => n.name).join(', ');
        const answer = (k, words) => `<button type="button" class="btn sm${k === 'keep' ? ' subtle' : ''}" data-act="xb-q-answer" data-link="${Number(a.refund_link_id)}" data-answer="${esc(k)}">${words}</button>`;
        const ask = `<div class="xb-q-sug"><p class="xb-q-sug__t">Refund of a purchase already paid back: keep it, or To pay back?</p>
            <div class="xb-q-why"><span>refund of ${esc(shortDay(pu.date))}</span><span>${esc(short(a.rest_minor, r.currency))} more than is yours on it</span>${names ? `<span>${esc(names)}</span>` : ''}</div>
            <div class="xb-q-acts">${answer('keep', 'Keep it')}${answer('pay_back', 'To pay back')}</div></div>`;
        return itemHTML(r.description, `${shortDay(r.date)} · ${r.account_name || ''}`, short(Math.abs(r.amount_minor), r.currency), ask);
    }

    /** The step: every payback with a suggestion, every waiting payment with a row, then every
     *  refund ask; '' when there is none. */
    function stepHTML(lane, asks) {
        const cards = (lane.paybacks || []).filter(p => p.suggestions.length).map(paybackHTML)
            .concat((lane.waiting_offers || []).filter(w => w.rows.length).map(waitingHTML))
            .concat((asks || []).map(a => a.kind === 'paid_back' ? paidBackHTML(a) : a.kind === 'which' ? whichHTML(a) : '').filter(Boolean));
        if (!cards.length) return '';
        return `<section class="step xb-q-step" aria-label="Back for…">
            <h3 class="step-h"><span class="fold xb-q-fold">Back for… <span class="n">${cards.length}</span></span></h3>
            <div class="step-b">${cards.join('')}</div></section>`;
    }

    Expect.queue = { setOffRequest, attachRequest, handLinkRequest, answerRequest, merchantWord, plainIds, backForButton, stepHTML };

    // --- the slot --------------------------------------------------------------------------

    let shown = { paybacks: [], waiting_offers: [] };   // the lane read last drawn, for ✓'s request

    /** Back for… beside This was… on each plain row's card, once the lane is in the page. */
    function besideThisWas(ids, tries = 20) {
        if (typeof document === 'undefined' || !ids.length) return;
        const lane = document.getElementById('lane-in');
        if (!lane) { if (tries > 0) setTimeout(() => besideThisWas(ids, tries - 1), 50); return; }
        ids.forEach(id => {
            const btn = lane.querySelector(`[data-act="this-was"][data-tx="${Number(id)}"]`);
            if (btn && !btn.parentElement.querySelector('[data-xb-back-beside]')) btn.insertAdjacentHTML('afterend', backForButton(id));
        });
    }

    Expect.fill('queueStep', async () => {
        if (typeof get !== 'function') return '';
        let lane, asks;
        try {
            [lane, asks] = await Promise.all([
                get('/api/queue/paybacks', { fresh: true }),
                get('/api/refund-links/asks', { fresh: true }).then(d => d.asks || []),
            ]);
        } catch (_) {
            return '';   // the lane draws as it would without this step
        }
        shown = lane;
        setTimeout(() => besideThisWas(plainIds(lane)), 0);
        return stepHTML(lane, asks);
    });

    // --- the step's buttons (app.js's one click handler finds them by data-act) ------------

    if (typeof ACT === 'undefined') return;

    ACT['xb-q-setoff'] = async el => {
        const tx = Number(el.dataset.tx), x = Number(el.dataset.x);
        const pb = shown.paybacks.find(p => p.payment.id === tx);
        const s = pb && pb.suggestions.find(v => v.expectation_id === x);
        if (!s) return;
        el.disabled = true;
        const r = await send('POST', `/api/transactions/${tx}/set-offs`, setOffRequest(pb, s));
        if (!r.ok) { el.disabled = false; toast(r.data.error || 'That did not work; nothing was changed', { bad: true }); rerender(); return; }
        toast(Expect.setoff.doneToast(r.data, [s.title], 'back'), { undo: r.change });
        refreshFrame();
        rerender();
    };
    ACT['xb-q-attach'] = async el => {
        const id = Number(el.dataset.so), rowId = Number(el.dataset.row);
        const w = (shown.waiting_offers || []).find(v => v.set_off_id === id);
        const r = w && w.rows.find(v => v.id === rowId);
        if (!r) return;
        el.disabled = true;
        const res = await send('POST', `/api/set-offs/${id}/attach`, attachRequest(w, r));
        if (!res.ok) { el.disabled = false; toast(res.data.error || 'That did not work; nothing was changed', { bad: true }); rerender(); return; }
        const x = res.data.expectation, rest = res.data.waiting;
        toast(`${r.description}: ${short(x.amount_minor, x.currency)} expected back, closed at once${rest ? `; ${short(rest.amount_minor, rest.currency)} still waits` : ''}.`, { undo: res.change });
        refreshFrame();
        rerender();
    };
    ACT['xb-q-not'] = async el => {
        const r = await act('POST', `/api/transactions/${Number(el.dataset.tx)}/not-a-payback`, {},
            'Not a payback: fin will not suggest it again');
        if (r) rerender();
    };
    ACT['xb-q-refund'] = async el => {
        const ask = { refund: { id: Number(el.dataset.refund) } };
        const r = await act('POST', '/api/refund-links', handLinkRequest(ask, { id: Number(el.dataset.purchase) }),
            `Linked as the refund of ${el.dataset.day}`);
        if (r) rerender();
    };
    ACT['xb-q-answer'] = async el => {
        const answer = el.dataset.answer;
        const r = await act('POST', `/api/refund-links/${Number(el.dataset.link)}/answer`, answerRequest(answer),
            answer === 'keep' ? 'Kept as a refund' : 'Set to pay back');
        if (r) rerender();
    };
})();
