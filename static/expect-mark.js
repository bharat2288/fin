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
