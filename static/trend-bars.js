// The trend card's bars (fin-surfaces 37): stacked SVG bars per stack key, the dashed
// refund cap, negatives below the axis, the hatched ⧗ missing segment (part-hatched in a
// quarter), the outlined quarter under way, the no-rate mark and the one-book average line.
//
// Two halves. Trend.bars.model(payload, opts) works out every figure the bars show: the
// segment extents, the cap, the hatch, the hover lines and what a click does. It is pure
// and tested under node. draw(el, payload, opts) only turns that model into SVG; it is
// registered as the 'bars' mode and adds no figure of its own.
//
// payload is R1's shape (GET /api/trend). An account's money in / out bars are the line
// mode's (trend-line.js), under its balance line. opts:
//   books     the books showing (default: payload.books_on, which the card sends, else every book)
//   onMonth   (months, label, bucket) when a bar or segment narrows the rows; months is always
//             a list, a quarter's three or a month's one (the card's contract, shared with 'line')
//   onImport  () when a hatched segment, or the hover's Import, is pressed
//   onFetch   (month) when the hover's Fetch is pressed (the no-rate month)
//   width, height  the drawing size in px (default: el's width, 220)
//
// Globals from app.js, declared here and copied nowhere: money, esc.
/* global money, esc */
(function () {
    const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];
    const HATCH_TEXT = 'No statement in yet for this month on a card this page uses. Import to fill it.';
    const GLYPH = '⧗';

    const monthName = m => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
    const dayText = d => `${Number(d.slice(8, 10))} ${MONTHS[Number(d.slice(5, 7)) - 1].slice(0, 3)} ${d.slice(0, 4)}`;
    const inr = minor => money(minor, 'INR').replace(/\.00$/, '');
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const isQuarter = b => /-Q[1-4]$/.test(b.key) || (b.months || []).length > 1;

    /** "September 2026"; "August and September 2026"; "July to September 2026". */
    function monthsLabel(months) {
        if (!months.length) return '';
        if (months.length === 1) return monthName(months[0]);
        const year = months[0].slice(0, 4), first = MONTHS[Number(months[0].slice(5, 7)) - 1];
        const last = monthName(months[months.length - 1]);
        if (months[months.length - 1].slice(0, 4) !== year) return `${monthName(months[0])} to ${last}`;
        return months.length === 2 ? `${first} and ${last}` : `${first} to ${last}`;
    }

    /** A quarter still under way is outlined. The server says so (trend_span); when it
     *  does not, the same rule: the bucket holds the month under way and its quarter has
     *  months still to come. */
    function outlinedOf(b, quarter) {
        if (typeof b.outlined === 'boolean') return b.outlined;
        const last = (b.months || [])[b.months.length - 1] || '';
        return Boolean(quarter && b.so_far && Number(last.slice(5, 7)) % 3 !== 0);
    }

    function noteOf(b, outlined) {
        if (b.note) return b.note;
        return outlined ? `${plural(b.months.length, 'month')} of this quarter so far` : null;
    }

    const OTHER = /(^|\|)other$/;

    /** The colour each stack key takes, and the key the card shows for it. Stacked by
     *  book, a book's place (the legend's book swatches follow it) and no key. Stacked by
     *  type or merchant, one type or merchant takes one colour in every book, dealt in the
     *  order first seen; Other takes its own neutral tone and comes last in the key. */
    function tonesOf(payload) {
        const keys = payload.stack_keys || [];
        const byKey = {};
        if (!payload.stack || payload.stack === 'book') {
            keys.forEach((k, i) => { byKey[k.key] = `c${i % 4 + 1}`; });
            return { byKey, key: [] };
        }
        const seen = [], key = [];
        let other = null;
        keys.forEach(k => {
            if (OTHER.test(k.key)) { byKey[k.key] = 'other'; other = other || { label: k.label || 'Other', tone: 'other' }; return; }
            const name = k.label || k.key;
            let i = seen.indexOf(name);
            if (i < 0) { i = seen.push(name) - 1; key.push({ label: name, tone: `c${i % 4 + 1}` }); }
            byKey[k.key] = `c${i % 4 + 1}`;
        });
        return { byKey, key: other ? key.concat(other) : key };
    }

    function model(payload, opts = {}) {
        const cur = payload.currency || 'SGD';
        const tones = tonesOf(payload).byKey;
        const keyOrder = (payload.stack_keys || []).map(k => k.key);
        const labels = Object.fromEntries((payload.stack_keys || []).map(k => [k.key, k.label || k.key]));
        const bookLines = payload.books || [];
        const visible = opts.books && opts.books.length ? opts.books.slice()
            : payload.books_on && payload.books_on.length ? payload.books_on.slice() : bookLines.map(b => b.book);
        const shown = new Set(visible);
        const buckets = payload.buckets || [];
        const factor = payload.line_factor || (buckets.some(isQuarter) ? 3 : 1);
        const avgOf = Object.fromEntries(bookLines.map(b => [b.book, b.avg]));
        let max = 0, min = 0;

        const out = buckets.map(b => {
            const quarter = isQuarter(b);
            const months = b.months || [b.key];
            const missingFor = book => new Set((b.missing || {})[book] || []);
            const missingBooks = Object.keys(b.missing || {}).filter(k => shown.has(k) && (b.missing[k] || []).length);
            const missingMonths = months.filter(m => missingBooks.some(k => missingFor(k).has(m)));
            // A month narrows when some book showing has its statement in for it.
            const narrowable = months.filter(m => visible.some(k => !missingFor(k).has(m)));
            const narrow = ms => ms.length ? { kind: 'narrow', months: ms, label: monthsLabel(ms) } : { kind: 'import' };

            const keys = keyOrder.concat(Object.keys(b.by || {}).filter(k => !keyOrder.includes(k)));
            const segments = [];
            let top = 0, bottom = 0;
            keys.forEach(key => {
                const c = (b.by || {})[key];
                if (!c || !shown.has(c.book)) return;
                const miss = missingFor(c.book);
                const s = { key, label: labels[key] || key, book: c.book, tone: tones[key] || `c${segments.length % 4 + 1}`,
                    net: c.net, spent: c.spent, back: c.back,
                    cap: null, hatch: miss.size / months.length, placeholder: false, glyph: null,
                    click: narrow(months.filter(m => !miss.has(m))), hover: c.hover || null };
                if (c.net >= 0) {
                    s.y0 = top; s.y1 = top + c.net;
                    if (c.back > 0 && c.net > 0) s.cap = [s.y1, s.y1 + c.back];
                    top = s.cap ? s.cap[1] : s.y1;
                } else {
                    s.y1 = bottom; s.y0 = bottom + c.net;
                    bottom = s.y0;
                }
                segments.push(s);
            });
            // A missing book with no rows in the bucket: a hatched stand-in at the book's own
            // average per bar, drawn only and never a figure.
            missingBooks.forEach(book => {
                if (segments.some(s => s.book === book)) return;
                const height = Math.max(0, (avgOf[book] || 0) * factor);
                const miss = missingFor(book);
                segments.push({ key: book, label: book, book, net: null, spent: null, back: null,
                    y0: top, y1: top + height, cap: null, hatch: 1, placeholder: true, glyph: GLYPH,
                    click: narrow(months.filter(m => !miss.has(m))), hover: null });
                top += height;
            });
            max = Math.max(max, top); min = Math.min(min, bottom);

            const lines = segments.filter(s => !s.placeholder).map(s => s.hover
                || (s.back > 0 ? `${s.label} · spent ${money(s.spent, cur)} · refunded ${money(s.back, cur)} · net ${money(s.net, cur)}`
                    : `${s.label} · net ${money(s.net, cur)}`));
            const inrIn = segments.reduce((a, s) => a + (s.placeholder ? 0 : ((b.by[s.key] || {}).inr_minor || 0)), 0);
            if (b.rate && !b.no_rate && inrIn) {
                lines.push(b.rate.exact
                    ? `Includes ${inr(inrIn)} at the month-end rate ${b.rate.text} (${dayText(b.rate.date)})`
                    : `Includes ${inr(inrIn)} at ${b.rate.text}, the rate of ${dayText(b.rate.date)}`);
            }
            if (missingMonths.length) {
                lines.push(quarter ? `${plural(missingMonths.length, 'month')} in this quarter not imported` : HATCH_TEXT);
            }
            let fetch = null;
            if (b.no_rate) {
                const left = (payload.left_out || []).filter(l => l.kind === 'no_rate' && months.includes(l.month));
                left.forEach(l => lines.push(`${inr(l.amount_minor)} not counted: ${l.reason}. Fetch it.`));
                if (!left.length && inrIn) lines.push(`${inr(inrIn)} not counted: no saved rate for ${monthsLabel(months)}. Fetch it.`);
                fetch = (left[0] && left[0].month) || months[0];
            }
            const outlined = outlinedOf(b, quarter);
            const note = noteOf(b, outlined);
            if (note) lines.push(note);

            return { key: b.key, label: b.label, months, quarter, so_far: Boolean(b.so_far), outlined,
                warn: missingMonths.length > 0, no_rate_mark: Boolean(b.no_rate) && !missingMonths.length,
                segments, top, bottom,
                hover: { title: b.label, lines, import: missingMonths.length > 0, fetch },
                click: narrow(narrowable) };
        });

        let avgLine = null, avgMonthly = null;
        if (visible.length === 1 && avgOf[visible[0]] != null) {
            avgMonthly = avgOf[visible[0]];
            avgLine = avgMonthly * factor;
            max = Math.max(max, avgLine); min = Math.min(min, avgLine);
        }
        return { currency: cur, buckets: out, max, min, line_factor: factor, avg_line: avgLine,
            avg_monthly: avgMonthly, books: visible };
    }

    // ---- the drawing ------------------------------------------------------------------

    let seq = 0;

    function svgOf(m, payload, W, H, hatchId) {
        const cur = m.currency;
        const short = v => money(v, cur).replace(/\.00$/, '');
        const L = 64, R = 8, T = 16, B = 30;
        const pw = Math.max(1, W - L - R), ph = Math.max(1, H - T - B);
        let hi = m.max, lo = m.min;
        if (hi === lo) hi = lo + 100;
        const top = hi;
        hi += (hi - lo) * 0.06;
        const y = v => T + (hi - v) / (hi - lo) * ph;
        const n = Math.max(1, m.buckets.length), slot = pw / n;
        const bw = Math.max(4, Math.min(slot * 0.62, 48));
        // Ticks (11px monospace: 0.6em a character). The last bar and every bar marked
        // missing always carry one; the others are every k-th bar counted back from the
        // last, k the smallest at which none touch, and one that would touch a kept tick
        // is left out. The year rides on the first tick drawn and wherever the year turns.
        const bk = m.buckets, yearOf = b => b.key.slice(2, 4);
        const textsFor = idx => Object.fromEntries(idx.map((i, j) => {
            const b = bk[i], year = !b.quarter && (j === 0 || yearOf(b) !== yearOf(bk[idx[j - 1]]));
            return [i, (b.quarter ? b.label : (b.label || '').slice(0, 3)) + (year ? ` ${yearOf(b)}` : '') + (b.warn ? ` ${GLYPH}` : '')];
        }));
        const touch = (t, a, c) => Math.abs(c - a) * slot < (t[a].length + t[c].length) * 3.3 + 4;
        const must = new Set(bk.length ? [n - 1].concat(bk.map((b, i) => (b.warn ? i : -1)).filter(i => i >= 0)) : []);
        const pick = k => {
            let idx = bk.map((_, i) => i).filter(i => must.has(i) || (n - 1 - i) % k === 0);
            let t = textsFor(idx);
            idx = idx.filter((i, j) => must.has(i) || !((j > 0 && must.has(idx[j - 1]) && touch(t, idx[j - 1], i))
                || (j + 1 < idx.length && must.has(idx[j + 1]) && touch(t, i, idx[j + 1]))));
            t = textsFor(idx);
            return { t, ok: idx.every((i, j) => !j || !touch(t, idx[j - 1], i) || (must.has(i) && must.has(idx[j - 1]))) };
        };
        let ticks = pick(1);
        for (let k = 2; k <= n && !ticks.ok; k++) ticks = pick(k);
        const parts = [];
        parts.push(`<defs><pattern id="${hatchId}" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
            + `<rect width="6" height="6" class="trend-bars__hatch-ground"/><line x1="0" y1="0" x2="0" y2="6" class="trend-bars__hatch-line"/></pattern></defs>`);
        [top, 0].concat(lo < 0 ? [lo] : []).forEach(v => {
            parts.push(`<line class="${v === 0 ? 'trend-bars__base' : 'trend-bars__grid'}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`);
            parts.push(`<text class="trend-bars__axis" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${esc(short(v))}</text>`);
        });
        m.buckets.forEach((b, i) => {
            const x = L + i * slot + (slot - bw) / 2;
            const g = [`<g data-b="${i}" class="trend-bars__col${b.outlined ? ' trend-bars__col--open' : ''}">`];
            g.push(`<rect class="trend-bars__hit" x="${L + i * slot}" y="${T}" width="${slot}" height="${ph}" tabindex="0" role="button" aria-label="${esc([b.hover.title].concat(b.hover.lines).join('. '))}"/>`);
            b.segments.forEach((s, j) => {
                const hiY = y(Math.max(s.y0, s.y1)), drawn = Math.abs(y(s.y0) - y(s.y1));
                const h = Math.max(s.placeholder ? 8 : 1, drawn);
                const yy = hiY - (h - drawn);
                const tone = s.book === 'account' ? `trend-bars__seg--${s.key}` : `trend-bars__seg--${s.tone}`;
                g.push(`<g data-seg="${j}">`);
                if (s.placeholder) {
                    g.push(`<rect class="trend-bars__seg trend-bars__seg--missing" x="${x}" y="${yy}" width="${bw}" height="${h}" fill="url(#${hatchId})"/>`);
                    g.push(`<text class="trend-bars__glyph" x="${x + bw / 2}" y="${yy - 4}" text-anchor="middle">${GLYPH}</text>`);
                } else {
                    g.push(`<rect class="trend-bars__seg ${tone}" x="${x}" y="${yy}" width="${bw}" height="${h}"/>`);
                    if (s.hatch > 0) g.push(`<rect class="trend-bars__part-hatch" x="${x}" y="${yy}" width="${bw * Math.min(1, s.hatch)}" height="${h}" fill="url(#${hatchId})"/>`);
                    if (s.cap) g.push(`<rect class="trend-bars__cap" x="${x}" y="${y(s.cap[1])}" width="${bw}" height="${Math.max(1, y(s.cap[0]) - y(s.cap[1]))}"/>`);
                }
                g.push('</g>');
            });
            if (b.no_rate_mark) g.push(`<circle class="trend-bars__norate" cx="${x + bw / 2}" cy="${y(b.top) - 8}" r="3.5"><title>no rate</title></circle>`);
            if (ticks.t[i] !== undefined) {
                const cls = `trend-bars__tick${b.warn ? ' trend-bars__tick--warn' : ''}${b.so_far ? ' trend-bars__tick--sofar' : ''}`;
                g.push(`<text class="${cls}" x="${L + i * slot + slot / 2}" y="${H - 10}" text-anchor="middle">${esc(ticks.t[i])}</text>`);
            }
            g.push('</g>');
            parts.push(g.join(''));
        });
        if (m.avg_line != null) {
            parts.push(`<line class="trend-bars__avg" x1="${L}" x2="${W - R}" y1="${y(m.avg_line)}" y2="${y(m.avg_line)}"/>`);
            parts.push(`<text class="trend-bars__avg-label" x="${W - R}" y="${y(m.avg_line) - 4}" text-anchor="end">avg ${esc(money(m.avg_monthly, cur))}/mo</text>`);
        }
        const name = (payload.subject && payload.subject.name) || 'Trend';
        return `<svg class="trend-bars__svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="group" aria-label="${esc(name)}: bars">${parts.join('')}</svg>`;
    }

    function tipOf(b) {
        const acts = (b.hover.import ? '<button type="button" class="trend-bars__act" data-act="import">Import</button>' : '')
            + (b.hover.fetch ? '<button type="button" class="trend-bars__act" data-act="fetch">Fetch</button>' : '');
        return `<div class="trend-bars__tip-title">${esc(b.hover.title)}</div>`
            + b.hover.lines.map(l => `<div class="trend-bars__tip-line">${esc(l)}</div>`).join('')
            + (acts ? `<div class="trend-bars__tip-acts">${acts}</div>` : '');
    }

    function draw(el, payload, opts = {}) {
        const m = model(payload, opts);
        const W = opts.width || el.clientWidth || 640, H = opts.height || 220;
        const hatchId = `trend-bars-hatch-${++seq}`;
        el.innerHTML = `<div class="trend-bars">${svgOf(m, payload, W, H, hatchId)}<div class="trend-bars__tip" role="status" hidden></div></div>`;
        const tip = () => el.querySelector('.trend-bars__tip');
        let current = null;

        function act(a, b) {
            if (!a) return;
            if (a.kind === 'import') {
                if (opts.onImport) opts.onImport();
                else if (typeof location !== 'undefined') location.hash = '#/books/import';
            } else if (a.kind === 'narrow' && opts.onMonth) opts.onMonth(a.months, a.label, b);
        }
        function show(i, evt) {
            const b = m.buckets[i];
            const t = tip();
            if (!b || !t) return;
            current = b;
            t.innerHTML = tipOf(b);
            t.hidden = false;
            if (evt && evt.clientX !== undefined && el.getBoundingClientRect) {
                const r = el.getBoundingClientRect();
                t.style.left = `${Math.min(Math.max(0, evt.clientX - r.left + 12), Math.max(0, r.width - 280))}px`;
                t.style.top = `${Math.max(0, evt.clientY - r.top + 12)}px`;
            }
        }
        const colOf = e => (e.target && e.target.closest ? e.target.closest('[data-b]') : null);

        el.addEventListener('click', e => {
            const button = e.target.closest('[data-act]');
            if (button) {
                if (button.dataset.act === 'import') act({ kind: 'import' });
                else if (button.dataset.act === 'fetch' && opts.onFetch && current) opts.onFetch(current.hover.fetch);
                return;
            }
            const bEl = colOf(e);
            const b = bEl && m.buckets[Number(bEl.dataset.b)];
            if (!b) return;
            const sEl = e.target.closest('[data-seg]');
            act(sEl ? b.segments[Number(sEl.dataset.seg)].click : b.click, b);
        });
        el.addEventListener('mouseover', e => { const bEl = colOf(e); if (bEl) show(Number(bEl.dataset.b), e); });
        el.addEventListener('focusin', e => { const bEl = colOf(e); if (bEl) show(Number(bEl.dataset.b)); });
        el.addEventListener('mouseleave', () => { const t = tip(); if (t) t.hidden = true; });
        el.addEventListener('keydown', e => {
            if (e.key !== 'Enter' && e.key !== ' ') return;
            const bEl = colOf(e);
            const b = bEl && m.buckets[Number(bEl.dataset.b)];
            if (!b) return;
            e.preventDefault();
            act(b.click, b);
        });
        return { model: m };
    }

    window.Trend = window.Trend || {};
    window.Trend.bars = { model, draw, monthsLabel, key: payload => tonesOf(payload).key };
    if (typeof window.Trend.registerMode === 'function') window.Trend.registerMode('bars', draw);
})();
