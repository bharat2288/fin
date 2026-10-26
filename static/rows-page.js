// rows-page.js (ticket 19; fin-redesign spec Part 2, the Rows page): Books → Rows.
//
// Every row fin holds in one list, whatever its flow, the rows of merchants
// hidden from spending included: /api/transactions?every=true a page at a time,
// beside /api/rows/summary for the flow chips' counts, money in and money out
// in S$ (never a spending figure: a transfer counts on both sides) and the
// months and parties to pick. Each row shows its own amount and its S$ value
// from the server's one path (RUL-022); a row in another currency with no rate
// says so, never a guess. Filters: month, account, party, search, book and
// flow. A link in (#/books/rows?account=3&flow=movement&month=all) sets the
// filters it names. Every figure is the server's; this file only words them.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global registerRoute, booksNavExtra, booksNav, refs, get, esc, money, icon, day, monthName, plural, tag, pager,
   rowMark, pageLink, rowAmount, ACT, rerender, sortOf, sortServer, sortHead, sortPicker, SORT_REDRAW */
(function () {
    'use strict';

    // The flows as the page words them, in the chips' order (flow.py declares them).
    const FLOWS = [['expense', 'Spending'], ['refund', 'Refund'], ['income', 'Income'], ['transfer', 'Transfer'],
        ['payment', 'Card payment'], ['movement', 'Movement'], ['review', 'Waiting for a label']];
    const FLOW_WORD = Object.fromEntries(FLOWS);
    // The columns the server can sort by (its allowlist reaches SQL).
    const SORT = { date: { label: 'Date', kind: 'date' }, description: { label: 'Row', kind: 'text' },
        amount: { label: 'Amount', kind: 'money' } };
    const FILTERS = { month: null, account: '', party: '', book: '', flow: '', search: '' };
    const LINKED = { month: 'month', account: 'account', party: 'party', book: 'book', flow: 'flow', search: 'search' };

    // What the page keeps while the app is open: its filters and page. A view, never a figure.
    const state = { ...FILTERS, page: 1 };
    let linked = null;

    /** A link in sets the filters its query names, once per query; a bare
     *  #/books/rows after a link starts from no filters. */
    function linkIn() {
        const hash = String(location.hash || '');
        const q = hash.includes('?') ? hash.slice(hash.indexOf('?') + 1) : '';
        if (q === linked) return;
        const first = linked === null;
        linked = q;
        if (!q && first) return;
        Object.assign(state, FILTERS, { page: 1 });
        const p = new URLSearchParams(q);
        for (const [k, name] of Object.entries(LINKED)) if (p.get(name)) state[k] = p.get(name);
    }

    /** The filters as the server reads them; `month` 'all' reads every month. */
    function filterParams() {
        const p = new URLSearchParams();
        if (state.month && state.month !== 'all') p.set('month', state.month);
        if (state.account) p.set('account_id', state.account);
        if (state.party) p.set('party_id', state.party);
        if (state.book) p.set('book', state.book);
        if (state.flow) p.set('flow', state.flow);
        if (state.search) p.set('search', state.search);
        return p;
    }
    function listURL() {
        const p = filterParams();
        p.set('every', 'true');
        p.set('page', String(state.page));
        p.set('per_page', '50');
        const s = sortServer('rows', SORT);
        p.set('sort', s.sort);
        p.set('sort_dir', s.sort_dir);
        return `/api/transactions?${p}`;
    }

    // ---- the cells ---------------------------------------------------------------------

    function flowPill(row) {
        const f = row.flow_type || 'expense';
        return `<span class="rp-flow rp-flow--${esc(f)}">${esc(f === 'review' ? 'Waiting' : FLOW_WORD[f] || f)}</span>`;
    }
    /** Where a row goes: a spending or refund row's book and type, the other
     *  side a transfer, card payment or movement names, an income row's kind. */
    function whereTo(row) {
        const f = row.flow_type || 'expense';
        if (f === 'review') return '<span class="muted">not labelled yet</span>';
        if (f === 'expense' || f === 'refund') {
            const type = row.display_type ? esc(row.display_type) : tag('nofig', 'no type');
            return `${esc(row.book || 'Household')} · ${type}`;
        }
        if (['transfer', 'payment', 'movement'].includes(f)) {
            if (!row.other_side_name) return '<span class="muted">no other side named</span>';
            const coming = row.amount_minor < 0;
            const word = coming ? 'from' : (f === 'movement' ? 'into' : 'to');
            const side = row.other_side_id ? pageLink('account', row.other_side_id, row.other_side_name) : esc(row.other_side_name);
            return `${word} ${side}`;
        }
        return row.display_type ? esc(row.display_type) : '<span class="muted">·</span>';
    }
    /** The row's S$ value on the one path: its own amount for an S$ row, the
     *  server's value for another currency, or "no rate" when it has none. */
    function sgdCell(row) {
        const cur = row.currency || 'SGD';
        if (cur === 'SGD') return `<span class="muted num rp-same">${esc(money(Math.abs(row.amount_minor), 'SGD'))}</span>`;
        if (!Number.isInteger(row.sgd_minor)) return tag('nofig', 'no rate', { title: 'No saved rate for its day: left out of the S$ figures, never guessed' });
        return `<span class="num">≈ ${esc(money(Math.abs(row.sgd_minor), 'SGD'))}</span>`;
    }
    function rowTr(row) {
        const what = row.service_id ? pageLink('merchant', row.service_id, row.description) : esc(row.description);
        const account = row.account_id ? pageLink('account', row.account_id, row.account_name || '') : esc(row.account_name || '');
        const party = row.service_id && row.service_name ? pageLink('merchant', row.service_id, row.service_name) : '<span class="muted">·</span>';
        return `<tr class="clickable" data-act="row" data-tx="${row.id}">
            <td class="num rp-date">${esc(day(row.date, { year: state.month === 'all' }))}</td>
            <td class="rp-desc">${what}${rowMark(row.id)}${row.hidden ? ' <span class="tag rp-hidden">hidden from spending</span>' : ''}<div class="small muted">${account}</div></td>
            <td class="rp-flowcell">${flowPill(row)}</td>
            <td class="rp-where">${whereTo(row)}</td>
            <td class="rp-party">${party}</td>
            <td class="r rp-amt">${rowAmount(row.amount_minor, row.currency || 'SGD')}</td>
            <td class="r rp-sgd">${sgdCell(row)}</td></tr>`;
    }

    // ---- the page ----------------------------------------------------------------------

    function flowChips(sum) {
        const counts = sum.flows || {};
        const all = Object.values(counts).reduce((a, n) => a + n, 0);
        const chip = (v, label, n) => `<button type="button" class="chip rp-chip" data-act="rows-set" data-k="flow" data-v="${esc(v)}" aria-pressed="${String(state.flow === v)}">${esc(label)} <span class="rp-n">${n}</span></button>`;
        return [chip('', 'Every flow', all), ...FLOWS.map(([k, label]) => chip(k, label, counts[k] || 0))].join('');
    }
    function figures(sum, monthWords) {
        const look = [sum.waiting ? `waiting for a label` : 'nothing waiting', sum.unvalued ? `${sum.unvalued} with no rate` : ''].filter(Boolean).join(' · ');
        const tile = (label, figure, sub, cls = '') => `<div class="stat-tile${cls}"><span class="stat-tile__label">${label}</span>
            <div class="stat-tile__figure">${figure}</div><div class="stat-tile__sub">${sub}</div></div>`;
        return `<div class="stat-tiles rp-tiles">
            ${tile('Rows', String(sum.rows), esc(monthWords))}
            ${tile('Money in', esc(money(sum.in_sgd_minor, 'SGD')), 'every row coming in, transfers too', ' rp-in')}
            ${tile('Money out', esc(money(sum.out_sgd_minor, 'SGD')), 'every row going out; not spending')}
            ${tile('To look at', String(sum.waiting), esc(look))}</div>
            <p class="card-foot">Money in and money out count every row, so a transfer counts on both sides: they are never a spending figure. In S$, a row in another currency at the saved rate of its day${sum.unvalued ? '; a row with no rate for its month is left out of them' : ''}.</p>`;
    }
    function filtersCard(r, sum) {
        const months = sum.months || [];
        const monthOpts = [...months.map(m => [m, monthName(m, { short: true })]), ['all', 'Every month']];
        const accounts = (r.accounts || []).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
        const parties = (sum.parties || []).slice();
        if (state.party && !parties.some(p => String(p.id) === String(state.party))) parties.unshift({ id: state.party, name: 'The party picked' });
        const select = (k, label, first, opts) => `<label class="field rp-${k}"><span>${label}</span><select data-change="rows-pick" data-k="${k}">${first ? `<option value="">${first}</option>` : ''}
            ${opts.map(([v, words]) => `<option value="${esc(v)}"${String(v) === String(state[k] ?? '') ? ' selected' : ''}>${esc(words)}</option>`).join('')}</select></label>`;
        const books = [['', 'Any'], ...(r.books || []).map(b => [b.name, b.name])];
        const filtered = state.account || state.party || state.book || state.flow || state.search;
        return `<section class="card rp-filters" aria-label="Filters">
            <div class="rp-fields">
                ${select('month', 'Month', '', monthOpts)}
                ${select('account', 'Account', 'Every account', accounts.map(a => [a.id, a.name]))}
                ${select('party', 'Party', 'Every party', parties.map(p => [p.id, p.name]))}
                <label class="field rp-search"><span>Search</span><input type="search" data-change="rows-pick" data-k="search" value="${esc(state.search)}" placeholder="Wording, merchant or type"></label>
            </div>
            <div class="rp-group"><span class="sp-label">Book</span><div class="seg" role="group" aria-label="Book">${books.map(([v, l]) =>
                `<button type="button" data-act="rows-set" data-k="book" data-v="${esc(v)}" aria-pressed="${String(state.book === v)}">${esc(l)}</button>`).join('')}</div>
                <span class="small muted">Only spending and refund rows carry a book, so picking one leaves the other flows out.</span></div>
            <div class="rp-group"><span class="sp-label">Flow</span><div class="chips" role="group" aria-label="Flow">${flowChips(sum)}</div></div>
            ${filtered ? '<button type="button" class="link rp-clear" data-act="rows-clear">Clear the filters</button>' : ''}
        </section>`;
    }

    async function view() {
        linkIn();
        const r = await refs();
        if (!state.month) {
            const first = await get(`/api/rows/summary?${filterParams()}`);
            state.month = first.latest_month || 'all';
        }
        const [sum, list] = await Promise.all([get(`/api/rows/summary?${filterParams()}`), get(listURL())]);
        const rows = list.transactions || [];
        ACT.__rows = new Map(rows.map(x => [x.id, x]));
        const monthWords = state.month === 'all' ? 'every month' : monthName(state.month);
        const title = state.flow ? `${FLOW_WORD[state.flow] || state.flow} rows` : 'Every row';
        const head = ['date', 'description'].map(k => `<th>${sortHead('rows', k, SORT)}</th>`).join('')
            + '<th>Flow</th><th>Where it goes</th><th>Party</th>'
            + `<th class="r">${sortHead('rows', 'amount', SORT)}</th><th class="r">In S$</th>`;
        const body = rows.length ? rows.map(rowTr).join('')
            : `<tr><td colspan="7" class="empty">No rows match these filters.${state.month !== 'all' ? ' <button type="button" class="link" data-act="rows-pick-all">Look at every month</button>' : ''}</td></tr>`;
        return `${booksNav('rows')}
    <div class="page-head"><div><span class="eyebrow">Books</span><h1>Rows</h1><p>Every row fin holds, whatever its flow: spending, refunds, income, transfers, card payments, movements and rows waiting for a label.</p></div></div>
    ${filtersCard(r, sum)}
    <section class="card rp-figures" aria-label="What the list holds">${figures(sum, monthWords)}</section>
    <section class="card"><div class="section-header"><h2>${esc(title)}</h2><span class="section-header__aside">${plural(list.total || 0, 'row')} · tap a row for its sheet</span></div>
        ${sortPicker('rows', SORT, { own: 'Newest first' })}<div class="table-shell"><table class="t rows rp-rows"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>
        ${pager(list, 'rows-page', !!sortOf('rows').key)}</section>`;
    }

    ACT['rows-set'] = el => { state[el.dataset.k] = el.dataset.v || ''; state.page = 1; rerender(); };
    ACT['rows-pick'] = el => { state[el.dataset.k] = el.value || ''; state.page = 1; rerender(); };
    ACT['rows-pick-all'] = () => { state.month = 'all'; state.page = 1; rerender(); };
    ACT['rows-clear'] = () => { Object.assign(state, FILTERS, { month: state.month, page: 1 }); rerender(); };
    ACT['rows-page'] = el => { state.page = Number(el.dataset.page) || 1; rerender(); window.scrollTo(0, 0); };
    SORT_REDRAW.rows = () => { state.page = 1; rerender(); };

    // Leaving the page forgets the link it came in by, so the same link followed
    // again sets its filters again; a bare return keeps the filters as they were.
    window.addEventListener?.('hashchange', () => { if (!String(location.hash || '').startsWith('#/books/rows')) linked = null; });
    registerRoute(/^\/books\/rows$/, () => view());
    if (Array.isArray(window.booksNavExtra)) window.booksNavExtra.push(['rows', '#/books/rows', 'Rows', 'arrows-left-right', 'spending']);
})();
