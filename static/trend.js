// The trend pages' shared card (fin-surfaces 36; spec 15, Client C1 core).
//
// window.Trend holds the mode registry that trend-bars.js and trend-line.js
// register their drawers into, and Trend.card(el, payload, opts): the span
// chips, the Stack by control, the legend chips, the summary, the ₹ / S$
// switch and the no-rate Fetch around one drawn chart. Every figure comes
// from the server's payload (R1 /api/trend, R3 /api/accounts/<id>/balances);
// the card only filters by the books showing, words the figures, and, for a
// rupee account's S$ view, multiplies by the month-end rate the server named.
//
// It reads these app.js globals by name, at call time, and copies none:
/* global money, esc, store, act, chartTokens, icon, day, monthName, plural, monthEnd */
(function () {
    'use strict';

    const modes = {};
    const Trend = window.Trend = window.Trend || {};
    Trend.modes = modes;
    Trend.registerMode = function registerMode(name, draw) { modes[name] = draw; };

    // -----------------------------------------------------------------------
    // Span chips: remembered on the device for the trend pages (not Spending,
    // whose span lives in its own key), 12M when nothing or nothing valid is
    // there, or when device storage is not to be had (a private window).
    // -----------------------------------------------------------------------

    const SPANS = ['6M', '12M', 'YTD', '2Y', 'All'];
    const DEFAULT_SPAN = '12M';
    const SPAN_KEY = 'trend.span';

    function span() {
        let value = null;
        try { value = store.get(SPAN_KEY, null); } catch (_) { value = null; }
        return SPANS.includes(value) ? value : DEFAULT_SPAN;
    }
    function rememberSpan(value) {
        if (!SPANS.includes(value)) return;
        try { store.set(SPAN_KEY, value); } catch (_) { /* no device storage: the span is not kept */ }
    }
    function spanChips(current) {
        return SPANS.map(s => ({ span: s, pressed: s === current }));
    }

    // -----------------------------------------------------------------------
    // Figures in words
    // -----------------------------------------------------------------------

    /** money(), with whole rupees written without paise ("₹ 1,24,500"). */
    function fmt(minor, cur) {
        const text = money(minor, cur);
        return cur === 'INR' && Number.isInteger(minor) && minor % 100 === 0 ? text.replace(/\.00$/, '') : text;
    }

    /** A rupee (or other two-digit) amount in S$ minor units at a decimal-text
     *  rate, rounded half-even, as balance_sheet.valued rounds. */
    function toSgd(minor, rateText) {
        if (minor === null || minor === undefined || !rateText) return null;
        const [whole, frac = ''] = String(rateText).split('.');
        const den = 10n ** BigInt(frac.length);
        const num = BigInt(whole + frac);
        const product = BigInt(minor) * num;
        const neg = product < 0n;
        const abs = neg ? -product : product;
        let q = abs / den;
        const twice = (abs % den) * 2n;
        if (twice > den || (twice === den && q % 2n === 1n)) q += 1n;
        return Number(neg ? -q : q);
    }

    // -----------------------------------------------------------------------
    // Books on and off: one book always shows
    // -----------------------------------------------------------------------

    function bookNames(payload) { return (payload.books || []).map(b => b.book); }

    function legend(payload, hidden) {
        const off = new Set(hidden || []);
        const names = bookNames(payload);
        const on = names.filter(n => !off.has(n));
        return names.map(book => ({ book, on: !off.has(book), disabled: !off.has(book) && on.length === 1 }));
    }

    /** The hidden list after pressing a book's chip. The last book showing
     *  stays: the answer is the list unchanged. */
    function toggleBook(payload, hidden, book) {
        const off = new Set(hidden || []);
        const names = bookNames(payload);
        if (off.has(book)) off.delete(book);
        else if (names.includes(book) && names.filter(n => !off.has(n)).length > 1) off.add(book);
        return names.filter(n => off.has(n));
    }

    function only(obj, keep) {
        const out = {};
        Object.keys(obj || {}).forEach(k => { if (keep(obj[k], k)) out[k] = obj[k]; });
        return out;
    }

    /** What the drawer draws: the payload with the hidden books' segments
     *  and missing marks taken out, the scale's top and bottom over what
     *  shows, and, with one book showing, its average line (× 3 on quarters). */
    function view(payload, state) {
        const off = new Set((state && state.hidden) || []);
        const buckets = (payload.buckets || []).map(b => Object.assign({}, b, {
            by: only(b.by, part => !off.has(part.book)),
            missing: only(b.missing, (_, book) => !off.has(book)),
        }));
        let top = 0, bottom = 0;
        buckets.forEach(b => {
            const nets = Object.values(b.by).map(p => p.net);
            top = Math.max(top, nets.filter(n => n > 0).reduce((a, n) => a + n, 0));
            bottom = Math.min(bottom, nets.filter(n => n < 0).reduce((a, n) => a + n, 0));
        });
        const on = (payload.books || []).filter(b => !off.has(b.book));
        const factor = buckets.some(b => /-Q[1-4]$/.test(b.key)) ? 3 : 1;
        return Object.assign({}, payload, {
            buckets, hidden: [...off], books_on: on.map(b => b.book), top, bottom, line_factor: factor,
            avg_line: on.length === 1 && on[0].avg !== null && on[0].avg !== undefined ? on[0].avg * factor : null,
        });
    }

    // -----------------------------------------------------------------------
    // The summary: each book its own net and average, never a grand total
    // -----------------------------------------------------------------------

    function change(book, spanName) {
        const pct = book.vs_prev_pct;
        if (pct === null || pct === undefined) return null;
        const sign = pct > 0 ? '+' : (pct < 0 ? '−' : '');
        return { text: `vs previous ${spanName} ${sign}${Math.abs(pct)}%`, tone: pct > 0 ? 'rise' : (pct < 0 ? 'fall' : 'flat') };
    }

    function leftOutWords(payload) {
        const items = (payload.left_out || []).map(item => {
            const amount = fmt(item.amount_minor, item.currency);
            return item.kind === 'no_rate' ? `${amount} (${monthName(item.month)}, no rate)` : `${amount} (${item.reason})`;
        });
        return items.length ? `leaves out ${items.join(', ')}` : null;
    }

    /** {lines: [{book, text, change}], left_out, tail?}. opts.kind
     *  'merchant' adds the last row and each book's rows (case 22). */
    function summary(payload, state, opts) {
        const off = new Set((state && state.hidden) || []);
        const cur = payload.currency || 'SGD';
        const on = (payload.books || []).filter(b => !off.has(b.book));
        const lines = on.length === 1
            ? on.map(b => {
                const c = change(b, payload.span);
                const parts = [`Net ${fmt(b.net, cur)}`, `avg ${fmt(b.avg, cur)}/mo`];
                if (c) parts.push(c.text);
                parts.push(`spent ${fmt(b.spent, cur)}`, `refunded ${fmt(b.back, cur)}`);
                return { book: b.book, text: parts.join(' · '), change: c };
            })
            : on.map(b => ({ book: b.book, text: `${b.book} ${fmt(b.net, cur)} · avg ${fmt(b.avg, cur)}/mo`, change: null }));
        const out = { lines, left_out: leftOutWords(payload) };
        const kind = (opts && opts.kind) || null;
        if (kind === 'merchant') {
            const books = payload.books || [];
            const last = books.map(b => b.last_date).filter(Boolean).sort().pop();
            out.tail = [last ? `last row ${day(last)}` : null, ...books.map(b => `${b.book} ${plural(b.rows, 'row')}`)]
                .filter(Boolean).join(' · ');
        }
        return out;
    }

    // -----------------------------------------------------------------------
    // Stack by: shown only when the server offers two choices
    // -----------------------------------------------------------------------

    function stackControl(payload) {
        const options = payload.stack_options || [];
        if (options.length < 2) return null;
        return { current: payload.stack, options: options.map(v => ({ value: v, label: v.charAt(0).toUpperCase() + v.slice(1) })) };
    }

    // -----------------------------------------------------------------------
    // No saved rate: named, never guessed; Fetch is the operator's press
    // -----------------------------------------------------------------------

    function noRate(payload) {
        return (payload.left_out || []).filter(item => item.kind === 'no_rate').map(item => ({
            month: item.month, currency: item.currency,
            text: `${fmt(item.amount_minor, item.currency)} not counted: no saved rate for ${monthName(item.month)}.`,
        }));
    }

    /** The existing rates fetch for the month's end. A failure saves nothing
     *  and act() toasts the reason; the mark stays until a refresh. */
    async function fetchRate(currency, month, opts) {
        const r = await act('POST', '/api/rates/fetch', { currency, date: monthEnd(month) }, `Rate saved for ${monthName(month)}`);
        if (r && opts && typeof opts.onRefresh === 'function') opts.onRefresh();
        return !!r;
    }

    // -----------------------------------------------------------------------
    // ₹ / S$: a rupee account opens in ₹; its S$ view is each month at the
    // month-end rate the server named. An S$ account has no switch.
    // -----------------------------------------------------------------------

    function accountCurrency(payload) { return payload.account ? payload.account.currency : null; }

    function currencySwitch(payload, cur) {
        const own = accountCurrency(payload);
        if (!own || own === 'SGD') return null;
        return { current: cur || payload.cur || own, options: [{ value: own, label: own === 'INR' ? '₹' : own }, { value: 'SGD', label: 'S$' }] };
    }

    function inCurrency(payload, cur) {
        if (!cur || cur === payload.cur || cur !== 'SGD') return payload;
        const months = (payload.months || []).map(m => {
            const rate = m.rate && m.rate.text;
            return Object.assign({}, m, {
                balance: rate ? toSgd(m.balance, rate) : null, estimate: rate ? toSgd(m.estimate, rate) : null,
                in: rate ? toSgd(m.in, rate) : null, out: rate ? toSgd(m.out, rate) : null,
            });
        });
        return Object.assign({}, payload, { cur: 'SGD', months });
    }

    function accountSummary(payload, cur) {
        const head = payload.head;
        if (!head || head.balance === null || head.balance === undefined) return null;
        const own = accountCurrency(payload) || payload.cur;
        const m = (payload.months || []).find(x => x.month === head.date.slice(0, 7)) || {};
        if (!cur || cur === own || cur !== 'SGD') {
            return { text: `Balance ${fmt(head.balance, own)} on ${day(head.date)} · in ${fmt(m.in, own)} · out ${fmt(m.out, own)}`, note: null };
        }
        const rate = m.rate && m.rate.text;
        const at = rate ? ` at ${rate}` : '';
        return {
            text: `Balance ${fmt(toSgd(head.balance, rate), 'SGD')} on ${day(head.date)}${at} · in ${fmt(toSgd(m.in, rate), 'SGD')} · out ${fmt(toSgd(m.out, rate), 'SGD')}`,
            note: payload.note || null,
        };
    }

    // -----------------------------------------------------------------------
    // The card
    // -----------------------------------------------------------------------

    function seg(label, kind, options) {
        return `<div class="seg trend-seg" role="group" aria-label="${esc(label)}">${options.map(o =>
            `<button type="button" data-trend="${kind}" data-value="${esc(o.value)}" aria-pressed="${o.pressed ? 'true' : 'false'}">${esc(o.label)}</button>`).join('')}</div>`;
    }

    function colours() {
        try { return chartTokens().books; } catch (_) { return []; }
    }

    function lineHtml(line) {
        const text = esc(line.text);
        if (!line.change) return `<p class="trend-sum-line">${text}</p>`;
        const c = esc(line.change.text);
        return `<p class="trend-sum-line">${text.replace(c, `<span class="trend-change trend-${line.change.tone}">${c}</span>`)}</p>`;
    }

    function render(el) {
        const st = el.__trend;
        const { payload, opts } = st;
        const account = !!payload.account;
        const controls = [seg('Span', 'span', spanChips(payload.span).map(c => ({ value: c.span, label: c.span, pressed: c.pressed })))];
        const stack = account ? null : stackControl(payload);
        if (stack) {
            controls.push(`<div class="trend-stack"><span class="trend-label">Stack by</span>${seg('Stack by', 'stack',
                stack.options.map(o => ({ value: o.value, label: o.label, pressed: o.value === stack.current })))}</div>`);
        }
        const sw = account ? currencySwitch(payload, st.cur) : null;
        if (sw) controls.push(seg('Currency', 'cur', sw.options.map(o => ({ value: o.value, label: o.label, pressed: o.value === sw.current }))));

        const parts = [`<div class="trend-controls">${controls.join('')}</div>`];
        let drawn;
        if (account) {
            drawn = inCurrency(payload, st.cur);
            parts.push('<div class="trend-plot"></div>');
            const s = accountSummary(payload, st.cur);
            if (s) parts.push(`<div class="trend-summary"><p class="trend-sum-line">${esc(s.text)}</p>${s.note ? `<p class="trend-note">${esc(s.note)}</p>` : ''}</div>`);
        } else {
            const chips = legend(payload, st.hidden);
            const tones = colours();
            if (chips.length) {
                parts.push(`<div class="trend-legend" role="group" aria-label="Books">${chips.map((c, i) =>
                    `<button type="button" class="chip trend-book${c.on ? '' : ' is-out'}" data-trend="book" data-value="${esc(c.book)}" aria-pressed="${c.on ? 'true' : 'false'}"${c.disabled ? ' disabled title="One book always shows"' : ''}>` +
                    `<span class="trend-swatch"${tones[i] ? ` style="background:${esc(tones[i])}"` : ''}></span>${esc(c.book)}</button>`).join('')}</div>`);
            }
            drawn = view(payload, st);
            parts.push('<div class="trend-plot"></div>');
            const s = summary(payload, st, { kind: opts.kind || (payload.subject && payload.subject.kind) });
            parts.push(`<div class="trend-summary">${s.lines.map(lineHtml).join('')}` +
                `${s.tail ? `<p class="trend-tail">${esc(s.tail)}</p>` : ''}${s.left_out ? `<p class="trend-left-out">${esc(s.left_out)}</p>` : ''}</div>`);
            const marks = noRate(payload);
            if (marks.length) {
                const glyph = typeof icon === 'function' ? icon('arrows-clockwise') : '';
                parts.push(`<ul class="trend-norate">${marks.map(m => `<li><span>${esc(m.text)}</span>` +
                    `<button type="button" class="btn sm" data-trend="fetch" data-value="${esc(m.month)}" data-cur="${esc(m.currency)}">${glyph}Fetch it</button></li>`).join('')}</ul>`);
            }
        }
        el.innerHTML = `<div class="trend-card">${parts.join('')}</div>`;
        const plot = el.querySelector('.trend-plot');
        const draw = modes[opts.mode || 'bars'];
        if (plot && typeof draw === 'function') draw(plot, drawn, { onMonth: opts.onMonth, onImport: opts.onImport, fmt });
    }

    function onClick(el, e) {
        const control = e.target && e.target.closest ? e.target.closest('[data-trend]') : null;
        if (!control || control.disabled) return undefined;
        const st = el.__trend;
        const value = control.dataset.value;
        switch (control.dataset.trend) {
            case 'span':
                rememberSpan(value);
                if (typeof st.opts.onSpan === 'function') st.opts.onSpan(value);
                return undefined;
            case 'stack':
                if (typeof st.opts.onStack === 'function') st.opts.onStack(value);
                return undefined;
            case 'book':
                st.hidden = toggleBook(st.payload, st.hidden, value);
                render(el);
                return undefined;
            case 'cur':
                st.cur = value;
                render(el);
                return undefined;
            case 'fetch':
                return fetchRate(control.dataset.cur, value, st.opts);
            default:
                return undefined;
        }
    }

    /** Draw the card into `el`. opts: mode ('bars' | 'line'), kind
     *  ('merchant' adds the rows tail), onSpan(span), onStack(stack),
     *  onRefresh() after a rate is fetched, onMonth / onImport passed to the
     *  drawer. Called again on the same el (a new span's payload), it keeps
     *  the books taken off and the currency picked. */
    function card(el, payload, opts) {
        let st = el.__trend;
        if (!st) {
            st = el.__trend = { hidden: [], cur: null };
            el.addEventListener('click', e => onClick(el, e));
        }
        st.payload = payload;
        st.opts = opts || {};
        const names = bookNames(payload);
        st.hidden = (st.hidden || []).filter(b => names.includes(b));
        if (names.length && st.hidden.length >= names.length) st.hidden = [];
        if (!st.cur) st.cur = payload.cur || null;
        render(el);
        return { el, update: next => card(el, next, st.opts) };
    }

    Object.assign(Trend, {
        SPANS, span, rememberSpan, spanChips, fmt, toSgd, legend, toggleBook, view, summary,
        stackControl, noRate, fetchRate, currencySwitch, inCurrency, accountSummary, card,
    });

    // The dev fixture harness (tests/fixtures/trend/harness.html) draws through
    // this hook when the page that loads it also loads app.js's globals.
    if (!window.trendHarness) {
        window.trendHarness = {
            render(el, kind, payload) {
                if (kind === 'trend_merchant' || kind === 'trend_type') return card(el, payload, { mode: 'bars' });
                if (kind === 'balances_sgd' || kind === 'balances_inr') return card(el, payload, { mode: 'line' });
                el.textContent = JSON.stringify(payload).slice(0, 2000);
                return null;
            },
        };
    }
})();
