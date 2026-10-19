// types-over-time.js (fin-surfaces 47; spec 15, Client C5): Books → Types over time.
//
// One book at a time (a book picker, never two books' lines side by side), asked of R2
// (/api/trend/types) at the remembered trend span. The trend card (trend.js) is mounted
// on that payload: mode 'line' (trend-line.js) draws every type as a line, the top four
// in the colour slots the server keeps by type, a chip taking a line off and a tap on a
// line picking it out; this file adds a link icon beside each type's chip, to its page.
// The small-multiples toggle mounts the same card with this file's own 'multiples' mode:
// one small chart per type with rows in the span, on one shared scale or each its own,
// a tap opening the type page. Every figure is the server's; this file only words and
// draws them.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global money, esc, icon, get, refs, booksNav, afterRender */
(function () {
    'use strict';

    const Trend = window.Trend = window.Trend || {};
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    // What the page keeps while the app is open: the book shown, lines or small
    // multiples, and the multiples' scale. A view preference, never a figure.
    const page = { book: null, span: null, multiples: false, scale: 'shared' };

    // ---- figures in, figures out -------------------------------------------------------

    /** The R2 read for one book at the remembered trend span. */
    function request(book, span) {
        const s = span || (typeof Trend.span === 'function' ? Trend.span() : '12M');
        return `/api/trend/types?book=${encodeURIComponent(book)}&span=${encodeURIComponent(s)}`;
    }

    /** The book picker: one book pressed, the one asked for while it is still a book,
     *  else Household, else the first. */
    function bookChoices(books, current) {
        const names = (books || []).map(b => b.name);
        const pick = names.includes(current) ? current : (names.includes('Household') ? 'Household' : names[0]);
        return names.map(name => ({ name, pressed: name === pick }));
    }

    /** A type page's address, or null while type pages are off (pageLinkKinds.type
     *  unset, ticket 23's rule), so no link ever lands on a page that is not there. */
    function typeHref(id) {
        const kinds = window.pageLinkKinds;
        const to = kinds && typeof kinds.type === 'function' ? kinds.type(id) : null;
        return to || null;
    }

    /** The legend's links: each type's page, beside its chip. */
    function links(payload) {
        return (payload.types || []).map(t => ({ id: t.id, name: t.name, href: typeHref(t.id) }))
            .filter(x => x.href);
    }

    /** One small chart per type with rows in the span. scale 'shared': every chart tops
     *  at the highest point drawn on any (and reaches the lowest below zero); 'own': each
     *  at its own. What is drawn is the line mode's model of that one type. */
    function multiples(payload, scale) {
        const cur = payload.currency || 'SGD';
        const withRows = (payload.types || []).filter(t => (t.points || []).some(p => p.net !== null && p.net !== undefined));
        const charts = withRows.map(t => {
            const m = Trend.lineModel({ currency: cur, months: payload.months || [], types: [t] }, {});
            const l = m.lines[0];
            const values = l.points.filter(p => p.value !== null).map(p => p.value);
            return {
                id: t.id, name: t.name, slot: t.slot || null, colour: l.colour, net: t.net, net_text: money(t.net, cur),
                href: typeHref(t.id), months: m.months, points: l.points, solid: l.solid, dashed: l.dashed,
                own_top: values.length ? Math.max(0, ...values) : 0, own_bottom: values.length ? Math.min(0, ...values) : 0,
            };
        });
        const top = Math.max(0, ...charts.map(c => c.own_top));
        const bottom = Math.min(0, ...charts.map(c => c.own_bottom));
        return charts.map(c => {
            const t = scale === 'own' ? c.own_top : top;
            const b = scale === 'own' ? c.own_bottom : bottom;
            const out = Object.assign({}, c, { top: t, bottom: b, top_text: money(t, cur) });
            delete out.own_top; delete out.own_bottom;
            return out;
        });
    }

    // ---- the small multiples, a card mode ----------------------------------------------

    function f1(v) { return Number(v).toFixed(1); }

    function smallSvg(c) {
        const W = 220, H = 72, T = 4, B = 4, n = Math.max(1, c.months.length);
        const x = k => n === 1 ? W / 2 : 4 + k * (W - 8) / (n - 1);
        const span = c.top - c.bottom || 1;
        const y = v => T + (c.top - v) / span * (H - T - B);
        const idx = {};
        c.months.forEach((m, k) => { idx[m] = k; });
        const values = c.points.map(p => p.value);
        const path = run => run.map((m, j) => `${j ? 'L' : 'M'}${f1(x(idx[m]))},${f1(y(values[idx[m]]))}`).join('');
        let s = `<line class="trend-types__base" x1="0" x2="${W}" y1="${f1(y(0))}" y2="${f1(y(0))}"/>`;
        c.solid.forEach(r => {
            s += r.length > 1 ? `<path class="trend-types__ln" d="${path(r)}"/>`
                : `<circle class="trend-types__dot" cx="${f1(x(idx[r[0]]))}" cy="${f1(y(values[idx[r[0]]]))}" r="2"/>`;
        });
        c.dashed.forEach(r => { s += `<path class="trend-types__ln trend-types__ln--est" d="${path(r)}"/>`; });
        c.points.forEach((p, k) => { if (p.estimate) s += `<circle class="trend-types__est" cx="${f1(x(k))}" cy="${f1(y(p.value))}" r="2.5"/>`; });
        return `<svg class="trend-types__svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(c.name)} net per month">${s}</svg>`;
    }

    function range(months) {
        if (!months.length) return '';
        const w = m => { const [yy, nn] = m.split('-').map(Number); return `${MON[nn - 1]} ${String(yy).slice(2)}`; };
        return `${w(months[0])} – ${w(months[months.length - 1])}`;
    }

    function multiplesHtml(view) {
        const charts = multiples(view, page.scale);
        const cur = view.currency || 'SGD';
        const seg = `<div class="seg trend-seg" role="group" aria-label="Scale">${[['shared', 'Shared scale'], ['own', 'Each its own']].map(([v, l]) =>
            `<button type="button" data-types="scale" data-value="${v}" aria-pressed="${page.scale === v}">${l}</button>`).join('')}</div>`;
        if (!charts.length) return `<div class="trend-types">${seg}<p class="trend-types__empty">No ${esc(view.book || '')} rows in this span.</p></div>`;
        const cells = charts.map(c => {
            const inner = `<span class="trend-types__head"><span class="trend-types__name"><i></i>${esc(c.name)}</span><b>${esc(c.net_text)}</b></span>`
                + `<span class="trend-types__top">top ${esc(money(c.top, cur))}${c.bottom < 0 ? ` · low ${esc(money(c.bottom, cur))}` : ''}</span>${smallSvg(c)}`
                + `<span class="trend-types__range">${esc(range(c.months))}</span>`;
            const cls = `trend-types__cell trend-line--${c.colour}`;
            return c.href ? `<a class="${cls}" href="${esc(c.href)}" aria-label="${esc(c.name)}: open its page">${inner}</a>`
                : `<div class="${cls}">${inner}</div>`;
        }).join('');
        return `<div class="trend-types">${seg}<div class="trend-types__grid">${cells}</div></div>`;
    }

    /** The card's 'multiples' mode, on the card's contract: draw(el, view, {onMonth, onImport, fmt}). */
    function drawMultiples(el, view) {
        function render() {
            el.innerHTML = multiplesHtml(view);
            el.querySelectorAll('[data-types="scale"]').forEach(b => b.addEventListener('click', () => {
                page.scale = b.dataset.value === 'own' ? 'own' : 'shared';
                render();
            }));
        }
        render();
    }
    if (typeof Trend.registerMode === 'function') Trend.registerMode('multiples', drawMultiples);

    // ---- the legend's link icons -----------------------------------------------------------

    /** Put a link icon beside each type chip the line mode drew, once. The line mode redraws
     *  its markup on every chip press, pick and resize, so this runs after each redraw. */
    function decorate(host, payload) {
        const hrefs = new Map(links(payload).map(x => [String(x.id), x]));
        if (!hrefs.size) return;
        host.querySelectorAll('.trend-line__chip').forEach(chip => {
            if (chip.parentElement && chip.parentElement.classList.contains('trend-types-pair')) return;
            const x = hrefs.get(chip.dataset.id);
            if (!x) return;
            // Chip and link wrap as one; the chip keeps its own element, so its press still
            // takes the line off.
            const pair = document.createElement('span');
            pair.className = 'trend-types-pair';
            chip.before(pair);
            pair.appendChild(chip);
            const a = document.createElement('a');
            a.className = 'trend-types-link';
            a.href = x.href;
            a.setAttribute('aria-label', `Open ${x.name}'s page`);
            a.title = `Open ${x.name}'s page`;
            a.innerHTML = typeof icon === 'function' ? icon('arrow-square-out') : '';
            pair.appendChild(a);
        });
    }

    function watch(host) {
        if (host.__typesWatch || typeof MutationObserver === 'undefined') return;
        // childList over the subtree: a redraw replaces the plot's markup. Adding a link is
        // itself a change, after which every chip already has its link, so it settles.
        host.__typesWatch = new MutationObserver(() => decorate(host, host.__typesPayload || {}));
        host.__typesWatch.observe(host, { childList: true, subtree: true });
    }

    // ---- the page ------------------------------------------------------------------------

    /** Mount the card on one book's R2 payload: mode 'line', or 'multiples' when the toggle
     *  is on. opts: {multiples, onSpan}. */
    function show(host, payload, opts) {
        opts = opts || {};
        host.__typesPayload = payload;
        const mode = opts.multiples ? 'multiples' : 'line';
        const handle = Trend.card(host, payload, { mode, onSpan: opts.onSpan });
        if (mode === 'line' && typeof host.querySelectorAll === 'function') { watch(host); decorate(host, payload); }
        return handle;
    }

    function notice(text) {
        return `<div class="notice">${typeof icon === 'function' ? icon('info') : ''}<span>${esc(text)}</span></div>`;
    }

    function head(choices) {
        const picker = `<div class="seg" role="group" aria-label="Book">${choices.map(c =>
            `<button type="button" data-types="book" data-value="${esc(c.name)}" aria-pressed="${c.pressed}">${esc(c.name)}</button>`).join('')}</div>`;
        const toggle = `<div class="seg" role="group" aria-label="Show">${[['lines', 'Lines'], ['multiples', 'Small multiples']].map(([v, l]) =>
            `<button type="button" data-types="show" data-value="${v}" aria-pressed="${(v === 'multiples') === page.multiples}">${l}</button>`).join('')}</div>`;
        return `<div class="trend-types__bar"><div class="trend-types__group"><span class="trend-types__label">Book</span>${picker}</div>`
            + `<div class="trend-types__group"><span class="trend-types__label">Show</span>${toggle}</div></div>`;
    }

    async function load(host) {
        let payload;
        try { payload = await get(request(page.book, page.span), { fresh: true }); } catch (err) {
            host.innerHTML = notice(err && err.message ? err.message : 'fin could not read the type lines');
            return;
        }
        // The span the chip names, so a private window (no device storage) still switches.
        show(host, payload, { multiples: page.multiples, onSpan: span => { page.span = span; load(host); } });
    }

    function bind(root) {
        root.addEventListener('click', e => {
            const b = e.target && e.target.closest ? e.target.closest('[data-types="book"], [data-types="show"]') : null;
            if (!b) return;
            if (b.dataset.types === 'book') page.book = b.dataset.value;
            else page.multiples = b.dataset.value === 'multiples';
            root.querySelectorAll(`[data-types="${b.dataset.types}"]`).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            load(root.querySelector('.trend-types__card'));
        });
    }

    async function viewTypesOverTime() {
        const r = await refs();
        const choices = bookChoices(r.books, page.book);
        page.span = null;   // each visit opens at the remembered span
        page.book = (choices.find(c => c.pressed) || {}).name || null;
        afterRender(() => {
            const root = document.querySelector('.trend-types-page');
            if (!root || !page.book) return;
            bind(root);
            load(root.querySelector('.trend-types__card'));
        });
        return `${booksNav('types')}
    <div class="page-head"><div><span class="eyebrow">Books</span><h1>Types over time</h1><p>Each type’s spending, net of refunds, one book at a time. The top four are coloured; take any line off, or tap one to pick it out.</p></div></div>
    <div class="trend-types-page">${head(choices)}
        <section class="card trend-types__card">${page.book ? '' : notice('No book to show.')}</section></div>`;
    }

    window.TypesOverTime = { request, bookChoices, links, multiples, show };

    if (typeof window.registerRoute === 'function') window.registerRoute(/^\/books\/types$/, () => viewTypesOverTime());
    if (Array.isArray(window.booksNavExtra)) window.booksNavExtra.push(['types', '#/books/types', 'Types over time', 'clock']);
})();
