// A folio reference (fin-redesign 25; folio_link.py): one read-only link from a row, an account
// or a party to its folio record, an LP's page or a row of folio's ledger.
//
// app.js puts an empty slot where the link goes (window.folioSlot?.(subject, id): the row sheet,
// an account's page, a person's page, a merchant's page). Once a slot is in the page it asks
// GET /api/folio-links/<subject>/<id> and draws what the server says: the link, opening folio in
// a new tab, with Change and Take off; or "Link to folio". The address is pasted in place; the
// server reads it and refuses, in words, anything that is not a folio record. fin never reads folio.
(function () {
    'use strict';

    const SLOT = '.folio-slot[data-folio-subject]';
    const url = slot => `/api/folio-links/${slot.dataset.folioSubject}/${Number(slot.dataset.folioId)}`;
    // Only folio's own address, from the server, is drawn as a link.
    const isWeb = href => /^https?:\/\//i.test(String(href || ''));

    /** The empty slot a page holds for a subject's link. */
    window.folioSlot = function folioSlot(subject, id) {
        return `<div class="folio-slot" data-folio-subject="${esc(subject)}" data-folio-id="${Number(id)}"></div>`;
    };

    /** What a slot shows: the held link, or the offer to add one. */
    window.folioLinkHTML = function folioLinkHTML(subject, id, link) {
        if (!link || !isWeb(link.url)) {
            return `<button type="button" class="link small" data-act="folio-edit">${icon('plus')}Link to folio</button>`;
        }
        return `<span class="folio-ref"><a class="link" href="${esc(link.url)}" target="_blank" rel="noopener noreferrer">${esc(link.words)}${icon('arrow-square-out')}</a>`
            + ` <button type="button" class="quiet-word" data-act="folio-edit">Change</button>`
            + ` <button type="button" class="quiet-word" data-act="folio-off">Take off</button></span>`;
    };

    function editorHTML(slot) {
        return `<div class="folio-edit"><label class="field"><span>folio address</span>
            <input type="text" class="folio-edit__input" placeholder="the address of an LP's page or a ledger row, or LP 18" value="${esc(slot.dataset.folioUrl || '')}"></label>
            <div class="row"><button type="button" class="btn sm primary" data-act="folio-save">Save</button>
            <button type="button" class="btn sm" data-act="folio-cancel">Cancel</button></div></div>`;
    }

    function draw(slot, link) {
        slot.dataset.folioUrl = link && isWeb(link.url) ? link.url : '';
        slot.innerHTML = window.folioLinkHTML(slot.dataset.folioSubject, slot.dataset.folioId, link);
    }

    async function fill(slot) {
        if (slot.dataset.folioDone) return;
        slot.dataset.folioDone = '1';
        try { draw(slot, (await get(url(slot), { fresh: true })).link); }
        catch (_) { slot.innerHTML = ''; }  // a subject gone says so on its own page
    }

    const slotOf = el => el.closest(SLOT);
    ACT['folio-edit'] = el => {
        const slot = slotOf(el);
        slot.innerHTML = editorHTML(slot);
        slot.querySelector('input').focus();
    };
    ACT['folio-cancel'] = async el => { const slot = slotOf(el); delete slot.dataset.folioDone; fill(slot); };
    ACT['folio-save'] = async el => {
        const slot = slotOf(el);
        const r = await act('PUT', url(slot), { link: slot.querySelector('input').value }, 'Linked to folio');
        if (r) draw(slot, r.data.link);
    };
    ACT['folio-off'] = async el => {
        const slot = slotOf(el);
        const r = await act('DELETE', url(slot), undefined, 'Took the folio link off');
        if (r) draw(slot, null);
    };

    // A slot is filled once it is in the page, whichever page or sheet drew it.
    const fillAll = () => document.querySelectorAll(`${SLOT}:not([data-folio-done])`).forEach(fill);
    document.addEventListener('DOMContentLoaded', () => {
        new MutationObserver(fillAll).observe(document.body, { childList: true, subtree: true });
        fillAll();
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Enter' && e.target.classList?.contains('folio-edit__input')) {
            e.preventDefault();
            ACT['folio-save'](e.target);
        }
    });
})();
