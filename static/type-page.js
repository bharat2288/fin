// The type page (fin-surfaces 45; spec 15, Client C3): #/books/type/<id>.
//
// Crumbs, then the trend card (stack by Book | Merchant, as the server offers),
// then the merchants for the span with their net and share, then the rows. A bar
// narrows the table and the rows to its months; a hatched segment goes to Import.
// The table follows one book: the first switched on, or the one picked when
// several are on. Its shares are of that book's net, and the rows below follow
// the same book. Every figure comes from the server (R1 GET /api/trend, and
// /api/transactions for the rows); the page only lays them out.
//
// Loading this file registers the route and switches type links on everywhere
// (pageLinkKinds.type), so a type name never opened a page that did not exist.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global registerRoute, pageLinkKinds, pageLink, afterRender, get, esc, money, icon, day, monthName, monthEnd, plural, spendRowTr, pager, ACT, SIGNS,
   sortOf, sortRows, sortHead, sortServer, SORT_REDRAW */
(function () {
    'use strict';

    const ROWS_PAGE = 50;
    // The page's state: the type shown, the span and stack the chart reads, the
    // book picked for the table (null: the first on), the months a bar narrowed
    // to (null: the whole span) and the rows' page.
    const state = { id: null, span: null, stack: 'book', picked: null, months: null, label: null, page: 1, payload: null };

    // The merchants sort here; the rows, a page at a time, on the server (each key is in its allowlist).
    const MERCHANT_SORT = { name: { label: 'Merchant', kind: 'text', of: r => r.name }, net: { label: 'Net', kind: 'money', of: r => r.net },
        share: { label: 'Share', kind: 'number', of: r => r.share_pct } };
    const ROW_SORT = { date: { label: 'Date', kind: 'date' }, description: { label: 'Statement text', kind: 'text' }, type: { label: 'Type', kind: 'text' },
        amount: { label: 'Amount', kind: 'money' } };

    const SPAN_WORDS = { '6M': 'the last 6 months', '12M': 'the last 12 months', YTD: 'this year so far', '2Y': 'the last 2 years', All: 'any month' };

    function query(params) {
        return Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
            .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
    }

    /** The chart's read; with book or months, the read the table takes its merchants from. */
    function trendUrl(s, { book = null, months = null } = {}) {
        return `/api/trend?${query({ subject: 'type', id: s.id, span: s.span, stack: s.stack, book, months: months ? months.join(',') : null })}`;
    }

    /** The span's first and last months, from the payload's buckets. */
    function spanMonths(payload) {
        const all = (payload.buckets || []).flatMap(b => b.months || []);
        return all.length ? [all[0], all[all.length - 1]] : null;
    }

    function rowsUrl(s, payload, book) {
        const ends = s.months ? [s.months[0], s.months[s.months.length - 1]] : spanMonths(payload);
        return `/api/transactions?${query({
            types: payload.subject.name, expense_only: 'true', book,
            start: ends ? `${ends[0]}-01` : null, end: ends ? monthEnd(ends[1]) : null,
            ...sortServer('tp-rows', ROW_SORT), per_page: ROWS_PAGE, page: s.page })}`;
    }

    /** The book the table follows: the one picked while it is on, else the first on. */
    function tableBook(books, on, picked) {
        const showing = books.filter(b => on.includes(b));
        if (picked && showing.includes(picked)) return picked;
        return showing[0] || books[0] || null;
    }

    function emptyWords(payload) {
        const name = payload.subject ? payload.subject.name : 'this type';
        const last = payload.empty && payload.empty.last_date;
        return `No ${name} rows in ${SPAN_WORDS[payload.span] || 'this span'}${last ? `; last row ${day(last)}` : ''}.`;
    }

    // The no-rows line stands where the chart would, with the card's span chips still live.
    if (window.Trend) {
        window.Trend.registerMode('type-empty', (el, view) => {
            el.innerHTML = `<p class="tp-empty">${esc(emptyWords(view))}</p>`;
        });
    }

    function booksOf(payload) { return (payload.books || []).map(b => b.book); }

    /** The books whose chips are pressed on the card; every book before it draws. */
    function booksOn(payload) {
        const host = document.getElementById('tp-trend');
        const pressed = host && host.querySelectorAll
            ? [...host.querySelectorAll('[data-trend="book"][aria-pressed="true"]')].map(b => b.dataset.value) : [];
        return pressed.length ? pressed : booksOf(payload);
    }

    function paidIn(cur) {
        return cur && cur !== 'SGD' ? ` <span class="tp-paid">paid in ${esc((typeof SIGNS !== 'undefined' && SIGNS[cur]) || cur)}</span>` : '';
    }

    function shareCell(pct) {
        if (pct === null || pct === undefined || pct < 0) return '<td class="tp-share"></td>';
        return `<td class="tp-share"><span class="tp-bar" aria-hidden="true"><i style="width:${Math.min(100, pct)}%"></i></span><span class="num">${pct}%</span></td>`;
    }

    /** The merchants card: one book's merchants, largest first, the others last. */
    function merchantsHtml(payload, table, ctx) {
        const m = table || { book: ctx.book, rows: [], others: null };
        const cur = payload.currency || 'SGD';
        const book = m.book || ctx.book || '';
        const picker = ctx.on.length > 1
            ? `<label class="field tp-pick"><span class="sr-only">Book for the merchants</span><select class="sel" data-change="tp-book">${ctx.on.map(b =>
                `<option value="${esc(b)}"${b === book ? ' selected' : ''}>${esc(b)}</option>`).join('')}</select></label>` : '';
        const rows = sortRows('tp-merchants', m.rows || [], MERCHANT_SORT).map(r => `<tr><td>${r.id ? pageLink('merchant', r.id, r.name) : esc(r.name)}${paidIn(r.paid_in)}</td>
            <td class="r num">${esc(money(r.net, cur))}</td>${shareCell(r.share_pct)}</tr>`);
        if (m.others && m.others.count) {
            rows.push(`<tr class="tp-others"><td>${esc(plural(m.others.count, 'other'))}</td>
                <td class="r num">${esc(money(m.others.net, cur))}</td>${shareCell(m.others.share_pct)}</tr>`);
        }
        return `<section class="card tp-merchants" aria-labelledby="tp-m-h"><div class="card-head"><div><h2 id="tp-m-h">Merchants in ${esc(payload.subject.name)}</h2>
            <p>${esc(book)} · ${esc(ctx.label || payload.span)}${sortOf('tp-merchants').key ? '' : ' · largest first'}</p></div>${picker}</div>
            ${rows.length ? `<div class="table-wrap"><table class="t tp-t"><thead><tr><th scope="col">${sortHead('tp-merchants', 'name', MERCHANT_SORT)}</th><th scope="col" class="r">${sortHead('tp-merchants', 'net', MERCHANT_SORT)}</th><th scope="col">${sortHead('tp-merchants', 'share', MERCHANT_SORT)}</th></tr></thead>
            <tbody>${rows.join('')}</tbody></table></div>` : `<p class="card-empty">No ${esc(payload.subject.name)} rows in ${esc(book || 'this book')} here.</p>`}</section>`;
    }

    function rowsHtml(payload, list, book) {
        const body = list.transactions.length
            ? `<div class="table-wrap"><table class="t tp-rows"><caption class="sr-only">${esc(payload.subject.name)} rows</caption>
                <thead><tr>${Object.keys(ROW_SORT).map(k => `<th scope="col"${k === 'amount' ? ' class="r"' : ''}>${sortHead('tp-rows', k, ROW_SORT)}</th>`).join('')}</tr></thead>
                <tbody>${list.transactions.map(spendRowTr).join('')}</tbody></table></div>${pager(list, 'tp-page', !!sortOf('tp-rows').key)}`
            : '<p class="card-empty">No rows here.</p>';
        return `<section class="card tp-rows-card" aria-labelledby="tp-r-h"><div class="card-head"><div><h2 id="tp-r-h">Rows</h2>
            <p>${esc(book || '')}${sortOf('tp-rows').key ? '' : ' · newest first'}</p></div></div>${body}</section>`;
    }

    function narrowHtml() {
        if (!state.months) return '';
        const label = state.label || state.months.map(m => monthName(m)).join(', ');
        return `<p class="tp-narrow" role="status">Showing ${esc(label)} · <button type="button" class="link" data-act="tp-all">Show all</button></p>`;
    }

    /** Everything below the chart: the narrowing line, the merchants, the rows. Left out when the span has no rows. */
    async function drawBelow() {
        const payload = state.payload;
        const host = document.getElementById('tp-below');
        if (!payload || !host) return;
        if (payload.empty) { host.innerHTML = ''; return; }
        const on = booksOn(payload);
        const book = tableBook(booksOf(payload), on, state.picked);
        const own = payload.merchants && payload.merchants.book === book && !state.months;
        const [table, list] = await Promise.all([
            own ? payload.merchants : get(trendUrl(state, { book, months: state.months }), { fresh: true }).then(p => p.merchants || null),
            get(rowsUrl(state, payload, book), { fresh: true }),
        ]);
        host.innerHTML = narrowHtml() + merchantsHtml(payload, table, { book, on, label: state.months ? state.label : null }) + rowsHtml(payload, list, book);
    }

    function cardOpts(payload) {
        return {
            mode: payload.empty ? 'type-empty' : 'bars',
            onSpan: span => { state.span = span; return reload(); },
            onStack: stack => { state.stack = stack; return reload(); },
            onRefresh: () => reload(),
            onMonth: (months, label) => {
                state.months = months && months.length ? months.slice() : null;
                state.label = state.months ? label || null : null;
                state.page = 1;
                return drawBelow();
            },
            onImport: () => { location.hash = '#/books/import'; },
        };
    }

    function drawCard(payload) {
        const host = document.getElementById('tp-trend');
        if (!host || !window.Trend) return;
        const fresh = !host.__tp;
        window.Trend.card(host, payload, cardOpts(payload));
        if (!fresh) return;
        // After the card's own handler: a book taken off or put back may move the table to another book.
        host.__tp = { book: tableBook(booksOf(payload), booksOf(payload), state.picked) };
        host.addEventListener('click', e => {
            const c = e.target && e.target.closest ? e.target.closest('[data-trend="book"]') : null;
            if (!c) return;
            const now = tableBook(booksOf(state.payload), booksOn(state.payload), state.picked);
            if (now !== host.__tp.book) { host.__tp.book = now; drawBelow(); }
        });
    }

    /** A new span or stack: one read for the chart, then what is below it, the narrowing cleared. */
    async function reload() {
        Object.assign(state, { months: null, label: null, page: 1 });
        state.payload = await get(trendUrl(state), { fresh: true });
        drawCard(state.payload);
        return drawBelow();
    }

    function crumbs() {
        return '<nav class="crumbs" aria-label="Where"><a href="#/books">Books</a> › <a href="#/books/spending">Spending</a> › By type</nav>';
    }

    async function view(id) {
        if (state.id !== id) Object.assign(state, { id, stack: 'book', picked: null, months: null, label: null, page: 1 });
        state.span = window.Trend ? window.Trend.span() : '12M';
        let payload;
        try { payload = await get(trendUrl(state), { fresh: true }); } catch (err) {
            if (err.status !== 404) throw err;
            state.payload = null;
            return `<div class="tp">${crumbs()}<div class="page-head"><div><h1>Type</h1></div></div>
                <div class="notice">${icon('info')}<span>This type is no longer in fin. <a class="link" href="#/books/lists/types">Go to Lists</a></span></div></div>`;
        }
        state.payload = payload;
        state.stack = payload.stack || state.stack;
        afterRender(() => { drawCard(state.payload); return drawBelow(); });
        return `<div class="tp">${crumbs()}
            <div class="page-head"><div><h1>${esc(payload.subject.name)}</h1><p>A spending type · every book</p></div></div>
            <section class="card tp-trend" id="tp-trend" aria-label="${esc(payload.subject.name)}, net money out per month"></section>
            <div class="tp-below" id="tp-below"></div></div>`;
    }

    ACT['tp-book'] = el => { state.picked = el.value; state.page = 1; return drawBelow(); };
    ACT['tp-all'] = () => { state.months = null; state.label = null; state.page = 1; return drawBelow(); };
    SORT_REDRAW['tp-merchants'] = () => drawBelow();
    SORT_REDRAW['tp-rows'] = () => { state.page = 1; return drawBelow(); };
    ACT['tp-page'] = el => { state.page = Number(el.dataset.page); return drawBelow(); };

    registerRoute(/^\/books\/type\/(\d+)$/, m => view(Number(m[1])));
    pageLinkKinds.type = id => `#/books/type/${id}`;

    window.TypePage = { state, view, tableBook, emptyWords, trendUrl, rowsUrl, merchantsHtml };
})();
