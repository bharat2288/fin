// account-trend.js (fin-surfaces 46; spec 15, Client C4): the account page's trend.
//
// Fills ticket 23's slot in viewAccount (window.mountAccountTrend, called after the
// page renders): a card after the top cards with the month-end balance as a line and
// money in and money out beneath it, on the same months, each chart on its own scale.
// The figures are R3's (/api/accounts/<id>/balances); the card is trend.js's in its
// 'line' mode (trend-line.js), which draws the estimates, the hatch, the pre-2026 note
// and "No balance yet". This file adds only what is the account page's own:
//   - the head: "Balance S$ 55,000.00, 30 Sep 2026", the last month end R3 names (on
//     19 Oct no statement dated 31 Oct can exist, case 54);
//   - a split card's part says it charts the whole card (case 38);
//   - a click on a month's in / out bar sets accountPage.month and re-renders, so the
//     rows below narrow to it ("Showing September 2026 · Show all"); a hatched month
//     goes to Import instead (case 25);
//   - an account deleted elsewhere: R3 answers 404 and the page says so (case 49).
// The span is the trend pages' remembered one (Trend.span); a chip re-reads R3.
//
// Globals from app.js (declared, never copied): accountPage, get, rerender, esc, day,
// monthName, sentence, icon.
/* global accountPage, get, rerender, esc, day, monthName, sentence, icon */
(function () {
    'use strict';

    const GONE = 'This account is no longer in fin';

    function balancesUrl(id, span) {
        return `/api/accounts/${encodeURIComponent(id)}/balances?span=${encodeURIComponent(span)}`;
    }

    /** "Balance S$ 55,000.00, 30 Sep 2026", in the account's own currency; null with no balance. */
    function headText(payload) {
        const head = payload && payload.head;
        if (!head || head.balance === null || head.balance === undefined || !head.date) return null;
        const cur = (payload.account && payload.account.currency) || payload.cur || 'SGD';
        return `Balance ${window.Trend.fmt(head.balance, cur)}, ${day(head.date)}`;
    }

    /** What a split card's part says: R3's words for the whole card, or null. */
    function partOfText(payload) {
        const part = payload && payload.account && payload.account.part_of;
        if (!part) return null;
        const words = typeof part === 'string' ? part : part.text;
        return words ? sentence(words) : null;
    }

    function narrowHtml() {
        if (!accountPage.month) return '';
        return `<p class="acct-trend__narrow">Showing ${esc(monthName(accountPage.month))} · `
            + `<button type="button" class="link" data-acct-trend="all">Show all</button></p>`;
    }

    function goneHtml() {
        const glyph = typeof icon === 'function' ? icon('info') : '';
        return `<div class="notice acct-trend__gone">${glyph}<span>${esc(GONE)}. `
            + `<a class="link" href="#/books/lists/accounts">Go to Lists</a></span></div>`;
    }

    /** The line mode sends a list of months; an account's bars are months, so one. */
    function narrowTo(months) {
        const month = Array.isArray(months) ? months[0] : months;
        if (!month) return;
        accountPage.month = month;
        accountPage.page = 1;
        rerender();
    }

    function toImport() { location.hash = '#/books/import'; }

    function onSectionClick(e) {
        const control = e.target && e.target.closest ? e.target.closest('[data-acct-trend]') : null;
        if (!control) return;
        if (control.dataset.acctTrend === 'all') {
            accountPage.month = null;
            accountPage.page = 1;
            rerender();
        }
    }

    /** Read R3 for the account at the span and draw it into `section`. */
    async function show(section, id, span) {
        span = span || window.Trend.span();
        if (!section.__acctTrend) {
            section.__acctTrend = true;
            section.addEventListener('click', onSectionClick);
        }
        let payload;
        try {
            payload = await get(balancesUrl(id, span));
        } catch (err) {
            if (section.isConnected === false) return;
            if (err && err.status === 404) {
                const view = (section.closest && section.closest('#view')) || section;
                view.innerHTML = goneHtml();
                return;
            }
            section.innerHTML = `<p class="notice bad">${esc((err && err.message) || 'fin could not read this account\'s balances')}</p>`;
            return;
        }
        if (section.isConnected === false) return;
        const head = headText(payload);
        const part = partOfText(payload);
        section.innerHTML = `<div class="section-header"><h2>Balance and money in and out</h2>`
            + `${head ? `<span class="section-header__aside acct-trend__head">${esc(head)}</span>` : ''}</div>`
            + (part ? `<p class="acct-trend__part">${esc(part)}</p>` : '')
            + `<div class="acct-trend__card"></div>${narrowHtml()}`;
        window.Trend.card(section.querySelector('.acct-trend__card'), payload, {
            mode: 'line',
            onMonth: narrowTo,
            onImport: toImport,
            onSpan: next => show(section, id, next),
        });
    }

    /** 23's slot: a card after the account page's top cards. */
    function mount() {
        const view = document.querySelector('#view');
        const top = view && view.querySelector('.acct-top');
        if (!top || accountPage.id === undefined || accountPage.id === null) return undefined;
        let section = view.querySelector('.acct-trend');
        if (!section) {
            top.insertAdjacentHTML('afterend', '<section class="card acct-trend" aria-label="Balance and money in and out"></section>');
            section = view.querySelector('.acct-trend');
        }
        return show(section, accountPage.id);
    }

    window.AccountTrend = { show, headText, partOfText, balancesUrl };
    window.mountAccountTrend = mount;
})();
