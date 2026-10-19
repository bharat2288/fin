// Expect back (personal-ledger 23): a linked refund's "refund of 20 Jul" on the row sheet.
//
// Filled by personal-ledger 38. On a refund row's sheet the actions line (the rowActions slot)
// gets an empty cell; once the sheet is drawn the cell asks GET /api/refund-links?row=<id> and
// shows the server's words, a link to the purchase's own sheet. A refund with no link shows
// nothing. Every word and figure comes from the server's payload, never a formula here.
(function () {
    'use strict';

    const Expect = window.Expect;
    const CELL = '[data-xb-refund-of]';

    // Fill one cell from the server: "refund of 20 Jul · AMAZON.SG", the date opening the purchase.
    Expect.refundOf = async function refundOf(cell) {
        if (cell.dataset.xbDone) return;
        cell.dataset.xbDone = '1';
        const found = await get(`/api/refund-links?row=${cell.dataset.xbRefundOf}`, { fresh: true });
        if (!found || !found.refund_link) { cell.innerHTML = ''; return; }
        cell.innerHTML = `<button class="link" data-act="row" data-tx="${found.purchase.id}">${esc(found.words)}</button>`
            + ` <span class="xb-refund-of__what">· ${esc(found.purchase.description)}</span>`;
    };

    Expect.fill('rowActions', row => {
        if (row.flow_type !== 'refund') return '';
        // The sheet's HTML is in the page by the time a microtask runs.
        if (typeof document !== 'undefined') {
            queueMicrotask(() => document.querySelectorAll(CELL).forEach(cell => { Expect.refundOf(cell); }));
        }
        return `<span class="xb-refund-of" data-xb-refund-of="${Number(row.id)}"></span>`;
    });
})();
