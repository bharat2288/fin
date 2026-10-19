// The merchant page's trend (fin-surfaces 44; spec 15, Client C2).
//
// Fills 23's mount slot: a card between the facts card and the Rows card, drawn by
// the shared trend card (trend.js, bars mode) from GET /api/trend?subject=merchant.
// The server decides every figure; this page only chooses what to show:
//   - few rows (under 3, or in under 2 months, counted over all time): one line, no chart;
//   - no rows in the span: the card with its span chips, the chart replaced by a line;
//   - otherwise the chart, stacked by book (or type, when the server offers both).
// A bar's click narrows the Rows card to its months through merchantPage.month
// ("Showing September 2026 · Show all"); a hatched segment goes to Import.
// The card element is kept across the page's redraws, so books taken off stay off.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global merchantPage, rerender, get, esc, money, day, plural, icon */
(function () {
    'use strict';

    const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];
    const monthWords = m => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
    const LISTS = '#/books/lists/merchants';
    const IMPORT = '#/books/import';

    // Which merchant the card is for, the stack and span picked on it, its element,
    // and a counter so a slow answer for an older ask is dropped. The span picked is
    // kept here as well as on the device, so a chip still switches in a private window.
    const state = { id: null, stack: null, span: null, host: null, seq: 0 };

    function url(id, span, stack) {
        return `/api/trend?subject=merchant&id=${encodeURIComponent(id)}&span=${encodeURIComponent(span)}`
            + (stack ? `&stack=${encodeURIComponent(stack)}` : '');
    }

    // -----------------------------------------------------------------------
    // The words
    // -----------------------------------------------------------------------

    /** Whole amounts without their cents: "S$ 84", "S$ 84.50". */
    const whole = (minor, cur) => money(minor, cur).replace(/\.00$/, '');

    /** "March 2026"; "February to April 2026"; "December 2025 to January 2026". */
    function stretch(first, last) {
        if (first === last) return monthWords(first);
        if (first.slice(0, 4) === last.slice(0, 4)) return `${MONTHS[Number(first.slice(5, 7)) - 1]} to ${monthWords(last)}`;
        return `${monthWords(first)} to ${monthWords(last)}`;
    }

    /** Cases 2-4: "2 rows, both in March 2026: S$ 84 net. A chart appears once ..." */
    function fewLine(few, cur) {
        const n = few.rows;
        const oneMonth = few.first_month === few.last_month;
        const where = oneMonth
            ? `${n === 1 ? 'in' : n === 2 ? 'both in' : 'all in'} ${monthWords(few.first_month)}`
            : stretch(few.first_month, few.last_month);
        return `${plural(n, 'row')}, ${where}: ${whole(few.net, cur)} net. A chart appears once this merchant has 3 rows across 2 months.`;
    }

    const SPAN_WORDS = { '6M': 'in the last 6 months', '12M': 'in the last 12 months', 'YTD': 'so far this year', '2Y': 'in the last 2 years' };

    /** Case 59: "No rows in the last 6 months; last row 22 Mar 2026." */
    function emptyLine(empty, span) {
        const last = empty && empty.last_date;
        if (!last) return 'No rows yet.';
        return `No rows ${SPAN_WORDS[span] || 'in this span'}; last row ${day(last)}.`;
    }

    /** What the card shows for a payload: {kind: 'few' | 'empty' | 'chart', text?}.
     *  The few-rows rule is over all time, so it comes before the span's emptiness. */
    function plan(payload) {
        const cur = payload.currency || 'SGD';
        if (payload.few) return { kind: 'few', text: fewLine(payload.few, cur) };
        if (payload.empty) return { kind: 'empty', text: emptyLine(payload.empty, payload.span) };
        return { kind: 'chart' };
    }

    // -----------------------------------------------------------------------
    // A bar narrows the rows (case 25)
    // -----------------------------------------------------------------------

    /** Every month from first to last: the rows are narrowed by one date range, so a
     *  list with a gap (a missing month inside a quarter) reads as the whole stretch. */
    function fill(first, last) {
        const out = [];
        let [y, m] = first.split('-').map(Number);
        const [ly, lm] = last.split('-').map(Number);
        while (y < ly || (y === ly && m <= lm)) {
            out.push(`${y}-${String(m).padStart(2, '0')}`);
            m += 1;
            if (m > 12) { m = 1; y += 1; }
        }
        return out;
    }

    function monthsOf(month) { return [].concat(month || []).filter(m => MONTH.test(m)).sort(); }

    /** "Showing September 2026", "Showing July to September 2026", or null. */
    function showing(month) {
        const ms = monthsOf(month);
        return ms.length ? `Showing ${stretch(ms[0], ms[ms.length - 1])}` : null;
    }

    /** onMonth(months): the rows below show those months, from their first page.
     *  Anything that is not a list of 'YYYY-MM' months is ignored. */
    function narrow(months) {
        const given = [].concat(months || []);
        const ms = monthsOf(given);
        if (!ms.length || ms.length !== given.length) return;
        const all = fill(ms[0], ms[ms.length - 1]);
        merchantPage.month = all.length === 1 ? all[0] : all;
        merchantPage.page = 1;
        rerender();
    }

    function showAll() {
        merchantPage.month = null;
        merchantPage.page = 1;
        rerender();
    }

    // -----------------------------------------------------------------------
    // The card
    // -----------------------------------------------------------------------

    function goneHtml() {
        return `<div class="notice">${typeof icon === 'function' ? icon('info') : ''}<span>This merchant is no longer in fin. ` +
            `<a class="link" href="${LISTS}">Go to Lists</a></span></div>`;
    }

    // An empty span keeps the card (its span chips stay live) and draws this line
    // where the chart would be.
    window.Trend.registerMode('merchant-empty', (plot, view) => {
        plot.innerHTML = `<p class="mp-trend__note">${esc(emptyLine(view.empty, view.span))}</p>`;
    });

    function cardOpts(id, mode) {
        return {
            mode, kind: 'merchant',
            onSpan: value => { state.span = value; return load(id, state.host); },
            onStack: value => { state.stack = value; return load(id, state.host); },
            onRefresh: () => load(id, state.host),
            onMonth: months => narrow(months),
            onImport: () => { location.hash = IMPORT; },
        };
    }

    function claim(id, host) {
        if (state.id !== id) { state.id = id; state.stack = null; state.span = null; }
        state.host = host;
    }

    /** Draw a payload into host: the few-rows line, or the card. */
    function show(host, payload, id) {
        claim(id, host);
        const p = plan(payload);
        if (p.kind === 'few') {
            host.innerHTML = `<p class="mp-trend__few">${esc(p.text)}</p>`;
            return p;
        }
        window.Trend.card(host, payload, cardOpts(id, p.kind === 'empty' ? 'merchant-empty' : 'bars'));
        return p;
    }

    /** Ask for the merchant's trend at the remembered span and draw it. A 404 is a
     *  merchant gone from fin (case 49); any other failure says the trend could not be read. */
    async function load(id, host) {
        claim(id, host);
        const seq = ++state.seq;
        let payload;
        try {
            payload = await get(url(id, state.span || window.Trend.span(), state.stack), { fresh: true });
        } catch (err) {
            if (seq !== state.seq) return null;
            host.innerHTML = err && err.status === 404 ? goneHtml()
                : '<p class="mp-trend__note">fin could not read this merchant’s trend. Reload to try again.</p>';
            return null;
        }
        if (seq !== state.seq || state.id !== id) return null;
        return show(host, payload, id);
    }

    // -----------------------------------------------------------------------
    // The mount: after each render of the merchant page
    // -----------------------------------------------------------------------

    /** The Rows card says what it is narrowed to, with Show all. */
    function markRows(rows) {
        const words = showing(merchantPage.month);
        if (!rows || !words) return;
        const head = rows.querySelector('.card-head');
        if (head) {
            const note = document.createElement('div');
            note.className = 'mp-trend__filt';
            note.setAttribute('role', 'status');
            note.innerHTML = `<span>${esc(words)}</span> · <button type="button" class="link">Show all</button>`;
            note.querySelector('button').addEventListener('click', showAll);
            head.appendChild(note);
        }
        const empty = rows.querySelector('.card-empty');
        if (empty) empty.textContent = `No rows from this merchant in ${words.replace(/^Showing /, '')}.`;
    }

    function mount() {
        const page = document.querySelector('#view .mp');
        const id = merchantPage.id;
        if (!page || id === null || id === undefined) return;
        const rows = page.querySelector('section[aria-labelledby="mp-rows"]');
        if (!rows) return;
        if (!state.host || state.id !== id) {
            const host = document.createElement('section');
            host.className = 'card mp-card mp-trend';
            host.setAttribute('aria-label', 'Over time');
            state.host = host;
            state.id = null;
        }
        rows.parentNode.insertBefore(state.host, rows);
        markRows(rows);
        load(id, state.host);
    }

    window.mountMerchantTrend = mount;
    window.MerchantTrend = { state, url, plan, fewLine, emptyLine, showing, narrow, showAll, show, load, mount };
})();
