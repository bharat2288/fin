// trend-line.js (fin-surfaces 38): the trend pages' line mode.
//
// Two shapes, one rule for gaps. An account's month-end balance with its money in and
// out beneath (the /api/accounts/<id>/balances shape), and one book's type lines (the
// /api/trend/types shape). The server works out every figure and every estimate; this
// file only decides what is drawn:
//   - a known month end or month is a solid point;
//   - an estimate is a hollow dot on dashed bridges, and only when a known point stands
//     on both sides of its run: the ends of a span are left open, whatever is offered;
//   - a month with nothing known is a gap, never 0, with the server's note when it has
//     one ("Balances start on 1 Jan 2026...") and "No balance before the first anchor"
//     for the stretch before an account's first balance;
//   - an account with no balance at all shows "No balance yet..." in place of the line;
//     its money in and out still draw.
// A type line takes its colour from its slot (the server keeps slots by type), so taking
// a line off never recolours another. A tap on a line picks it out and dims the rest.
//
// Globals from app.js (declared, never copied): money, esc.
/* global money, esc */
(function () {
    const Trend = window.Trend = window.Trend || {};

    const PRE_ANCHOR = 'No balance before the first anchor';
    const NO_BALANCE = 'No balance yet: import a statement or add an anchor';
    const MISSING_TEXT = 'No statement in yet for this month on a card this page uses. Import to fill it.';
    const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const MONTH_FULL = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
        'September', 'October', 'November', 'December'];

    // ---- the model: what is drawn, as figures -------------------------------------

    /** raw: [{month, known, est}] → points with the estimates that may be drawn, and the
     *  solid and dashed runs as month lists. An estimate is kept only inside a run of
     *  estimates with a known point on each side. */
    function shape(raw) {
        const n = raw.length;
        const keep = new Array(n).fill(false);
        let i = 0;
        while (i < n) {
            if (raw[i].known !== null || raw[i].est === null) { i++; continue; }
            let j = i;
            while (j + 1 < n && raw[j + 1].known === null && raw[j + 1].est !== null) j++;
            if (i > 0 && raw[i - 1].known !== null && j + 1 < n && raw[j + 1].known !== null) {
                for (let k = i; k <= j; k++) keep[k] = true;
            }
            i = j + 1;
        }
        const points = raw.map((r, k) => ({
            month: r.month,
            value: r.known !== null ? r.known : (keep[k] ? r.est : null),
            estimate: r.known === null && keep[k],
        }));
        const solid = [], dashed = [];
        let run = null;
        points.forEach((p, k) => {
            if (p.value !== null && !p.estimate) { (run = run || []).push(p.month); }
            else { if (run) solid.push(run); run = null; }
            if (p.estimate && !(k > 0 && points[k - 1].estimate)) {
                const bridge = [points[k - 1].month];
                let e = k;
                while (points[e].estimate) bridge.push(points[e++].month);
                bridge.push(points[e].month);
                dashed.push(bridge);
            }
        });
        if (run) solid.push(run);
        return { points, solid, dashed };
    }

    function nice(lo, hi, steps, withZero) {
        if (withZero) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
        if (!(hi > lo)) { const pad = Math.abs(hi) * 0.1 || 100; lo -= pad; hi += pad; }
        const raw = (hi - lo) / steps;
        const p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
        const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
        return { lo: Math.floor(lo / step) * step, hi: Math.ceil(hi / step) * step, step, zero: !!withZero };
    }

    function balanceModel(payload) {
        const cur = payload.cur || (payload.account && payload.account.currency) || 'SGD';
        const months = payload.months || [];
        const raw = months.map(m => ({
            month: m.month,
            known: m.balance === null || m.balance === undefined ? null : m.balance,
            est: m.estimate === null || m.estimate === undefined ? null : m.estimate,
        }));
        const s = shape(raw);
        const firstKnown = s.points.findIndex(p => p.value !== null && !p.estimate);
        const head = payload.head && payload.head.balance !== null && payload.head.balance !== undefined
            ? { balance: payload.head.balance, date: payload.head.date, text: money(payload.head.balance, cur) } : null;
        const empty = firstKnown < 0 && !head ? NO_BALANCE : null;
        // Gaps: runs of months with nothing drawn, so far aside. Each carries its note.
        const gaps = [];
        let g = null;
        s.points.forEach((p, k) => {
            const m = months[k];
            if (p.value === null && !m.so_far) {
                if (!g) g = { months: [], note: null, start: k, plain: false };
                g.months.push(p.month);
                if (!g.note && m.note) g.note = m.note;
                if (!m.missing) g.plain = true;
            } else if (g) { gaps.push(g); g = null; }
        });
        if (g) gaps.push(g);
        gaps.forEach(x => {
            if (!x.note && !empty && x.start === 0 && firstKnown > 0 && x.plain) x.note = PRE_ANCHOR;
            delete x.start; delete x.plain;
        });
        const values = s.points.filter(p => p.value !== null).map(p => p.value);
        const scale = values.length ? nice(Math.min(...values), Math.max(...values), 3, false)
            : { lo: 0, hi: 1, step: 1, zero: false };
        const bars = months.map(m => ({ month: m.month, in: m.in || 0, out: m.out || 0,
            hatched: !!m.missing, so_far: !!m.so_far, note: m.note || null, rate: m.rate || null }));
        const barMax = Math.max(1, ...bars.map(b => Math.max(Math.abs(b.in), Math.abs(b.out))));
        return { kind: 'balance', cur, months: months.map(m => m.month), points: s.points, solid: s.solid,
            dashed: s.dashed, gaps: gaps.filter(x => x.note), missing: months.filter(m => m.missing).map(m => m.month),
            empty, head, note: payload.note || null, scale, bars, bar_scale: nice(0, barMax, 2, false),
            account: payload.account || null };
    }

    function typesModel(payload, state) {
        const cur = payload.currency || 'SGD';
        const off = new Set(state.off || []);
        const months = payload.months || [];
        let picked = state.picked === undefined ? null : state.picked;
        const types = payload.types || [];
        if (picked !== null && (off.has(picked) || !types.some(t => t.id === picked))) picked = null;
        const lines = types.map(t => {
            const byMonth = {};
            (t.points || []).forEach(p => { byMonth[p.month] = p; });
            const raw = months.map(m => {
                const p = byMonth[m] || { month: m, net: null, estimate: null, missing: false };
                const est = p.estimate === null || p.estimate === undefined ? null : p.estimate;
                // A missing month is drawn as its estimate, even when some rows arrived.
                const known = p.missing || p.net === null || p.net === undefined ? null : p.net;
                return { month: m, known, est: p.missing ? est : null };
            });
            const s = shape(raw);
            const isPicked = picked !== null && t.id === picked;
            return { id: t.id, name: t.name, slot: t.slot || null, net: t.net, net_text: money(t.net, cur),
                colour: t.slot ? 's' + t.slot : (isPicked ? 'picked' : 'grey'),
                off: off.has(t.id), picked: isPicked, dim: picked !== null && !isPicked,
                points: s.points, solid: s.solid, dashed: s.dashed };
        });
        // lines keep the payload's order: the legend's chips follow it. The drawing puts the
        // picked line last (typesSvg's own order), so the legend never moves.
        const values = lines.filter(l => !l.off).flatMap(l => l.points.filter(p => p.value !== null).map(p => p.value));
        const scale = values.length ? nice(Math.min(...values), Math.max(...values), 4, true)
            : { lo: 0, hi: 1, step: 1, zero: true };
        const missing = months.filter(m => types.some(t => (t.points || []).some(p => p.month === m && p.missing)));
        return { kind: 'types', cur, book: payload.book || null, months, lines, scale, missing,
            picked, any_off: lines.some(l => l.off) };
    }

    /** What the line mode draws for a payload, as figures. state: {off: [type ids],
     *  picked: type id | null}; the balance shape ignores it. */
    Trend.lineModel = function lineModel(payload, state) {
        state = state || {};
        return payload && Array.isArray(payload.types) ? typesModel(payload, state) : balanceModel(payload || {});
    };

    // ---- the drawing ----------------------------------------------------------------

    let uid = 0;

    function label(m, k) {
        const [y, n] = m.split('-').map(Number);
        return n === 1 || k === 0 ? `${MON[n - 1]} ${String(y).slice(2)}` : MON[n - 1];
    }
    function full(m) { const [y, n] = m.split('-').map(Number); return `${MONTH_FULL[n - 1]} ${y}`; }
    function sign(cur) { return String(money(0, cur)).split(' ')[0]; }
    function short(v, cur) {
        const d = cur === 'JPY' ? 0 : 2;
        const major = v / Math.pow(10, d);
        const txt = new Intl.NumberFormat(cur === 'INR' ? 'en-IN' : 'en-US',
            { notation: 'compact', maximumFractionDigits: 1 }).format(Math.abs(major));
        return `${v < 0 ? '−' : ''}${sign(cur)} ${txt}`;
    }
    function f1(v) { return Number(v).toFixed(1); }

    function geometry(width, n, phone, right) {
        const L = phone ? 52 : 72, R = right, bw = (width - L - R) / Math.max(1, n);
        return { L, R, bw, x: k => L + k * bw + bw / 2 };
    }
    function ticks(sc) {
        const out = [];
        for (let v = sc.lo; v <= sc.hi + sc.step / 1e6; v += sc.step) out.push(Math.round(v));
        return out;
    }
    function xLabels(months, g, y, phone) {
        const every = phone && months.length > 7 ? (months.length > 14 ? 3 : 2) : (months.length > 14 ? 2 : 1);
        const last = months.length - 1;
        return months.map((m, k) => (last - k) % every === 0
            ? `<text class="trend-line__x" x="${f1(g.x(k))}" y="${y}" text-anchor="middle">${label(m, k)}</text>` : '').join('');
    }
    function hatchDef(id) {
        return `<pattern id="${id}" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">`
            + `<rect class="trend-line__hatch-ground" width="7" height="7"/><line class="trend-line__hatch-line" x1="0" y1="0" x2="0" y2="7"/></pattern>`;
    }
    function pathOf(run, idx, g, y, values) {
        return run.map((m, j) => `${j ? 'L' : 'M'}${f1(g.x(idx[m]))},${f1(y(values[idx[m]]))}`).join('');
    }

    function balanceSvg(m, width) {
        const phone = width < 520, n = m.months.length, idx = {};
        m.months.forEach((x, k) => { idx[x] = k; });
        const g = geometry(width, n, phone, 12), T = 18, B = 24, id = 'tl' + (++uid);
        const values = m.points.map(p => p.value);
        let top = '';
        if (m.empty) {
            top = `<p class="trend-line__empty">${esc(m.empty)}</p>`;
        } else {
            const H = phone ? 170 : 200, sc = m.scale, y = v => T + (sc.hi - v) / (sc.hi - sc.lo) * (H - T - B);
            let s = `<defs>${hatchDef(id)}</defs>`;
            ticks(sc).forEach(v => { s += `<line class="trend-line__grid" x1="${g.L}" x2="${f1(width - g.R)}" y1="${f1(y(v))}" y2="${f1(y(v))}"/>`
                + `<text class="trend-line__y" x="${g.L - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${esc(short(v, m.cur))}</text>`; });
            m.gaps.forEach(gp => {
                const a = idx[gp.months[0]], b = idx[gp.months[gp.months.length - 1]];
                s += `<rect class="trend-line__gap" x="${f1(g.L + a * g.bw + 1)}" y="${T}" width="${f1((b - a + 1) * g.bw - 2)}" height="${H - T - B}" rx="3"><title>${esc(gp.note)}</title></rect>`;
            });
            m.missing.forEach(x => { const k = idx[x];
                s += `<rect x="${f1(g.L + k * g.bw + 2)}" y="${T}" width="${f1(Math.max(1, g.bw - 4))}" height="${H - T - B}" rx="3" fill="url(#${id})"/>`
                    + `<text class="trend-line__miss" x="${f1(g.x(k))}" y="${T - 4}" text-anchor="middle">⧗</text>`; });
            m.solid.forEach(run => { s += `<path class="trend-line__ln trend-line--bal" d="${pathOf(run, idx, g, y, values)}"/>`; });
            m.dashed.forEach(run => { s += `<path class="trend-line__ln trend-line__ln--est trend-line--bal" d="${pathOf(run, idx, g, y, values)}"/>`; });
            m.points.forEach((p, k) => {
                if (p.value === null) return;
                if (p.estimate) s += `<circle class="trend-line__est trend-line--bal" cx="${f1(g.x(k))}" cy="${f1(y(p.value))}" r="4"/>`;
                else if (n <= 12 || k === n - 1 || values[k + 1] === null) s += `<circle class="trend-line__dot trend-line--bal" cx="${f1(g.x(k))}" cy="${f1(y(p.value))}" r="3"/>`;
            });
            const last = m.points.map((p, k) => [p, k]).filter(([p]) => p.value !== null && !p.estimate).pop();
            if (last) s += `<text class="trend-line__val" x="${f1(g.x(last[1]))}" y="${f1(y(last[0].value) - 10)}" text-anchor="end">${esc(money(last[0].value, m.cur))}</text>`;
            s += xLabels(m.months, g, H - B + 15, phone);
            m.months.forEach((x, k) => { s += `<rect class="trend-line__hit" data-k="${k}" x="${f1(g.L + k * g.bw)}" y="${T}" width="${f1(g.bw)}" height="${H - T - B}"/>`; });
            top = `<svg class="trend-line__svg" viewBox="0 0 ${width} ${H}" height="${H}" role="img" aria-label="Balance at each month end">${s}</svg>`;
        }
        const notes = m.gaps.map(gp => `<p class="trend-line__note">${esc(label(gp.months[0], 0))}${gp.months.length > 1 ? ' to ' + esc(label(gp.months[gp.months.length - 1], 0)) : ''}: ${esc(gp.note)}</p>`).join('');
        // Money in up, money out down, from one baseline: its own chart, its own scale.
        const H2 = phone ? 150 : 170, mx = m.bar_scale.hi, y2 = v => T + (mx - v) / (2 * mx) * (H2 - T - B);
        const gap = Math.max(2, g.bw * 0.3), w = Math.max(1, g.bw - gap);
        let b = `<defs>${hatchDef(id + 'b')}</defs>`;
        [mx, 0, -mx].forEach(v => { b += `<line class="${v ? 'trend-line__grid' : 'trend-line__base'}" x1="${g.L}" x2="${f1(width - g.R)}" y1="${f1(y2(v))}" y2="${f1(y2(v))}"/>`
            + `<text class="trend-line__y" x="${g.L - 6}" y="${f1(y2(v) + 4)}" text-anchor="end">${v ? esc((v > 0 ? '+' : '') + short(v, m.cur)) : '0'}</text>`; });
        m.bars.forEach((bar, k) => {
            const bx = f1(g.L + k * g.bw + gap / 2), cls = bar.hatched ? ' trend-line__bar--hatched' : (bar.so_far ? ' trend-line__bar--so-far' : '');
            const fill = bar.hatched ? ` style="fill:url(#${id}b)"` : '';
            const hIn = Math.max(0, y2(0) - y2(Math.abs(bar.in))), hOut = Math.max(0, y2(-Math.abs(bar.out)) - y2(0));
            if (bar.hatched && !bar.in && !bar.out) {
                b += `<rect class="trend-line__bar--hatched" x="${f1(g.L + k * g.bw + 2)}" y="${T}" width="${f1(Math.max(1, g.bw - 4))}" height="${H2 - T - B}" rx="3" style="fill:url(#${id}b)"/>`;
            } else {
                b += `<rect class="trend-line__bar trend-line__bar--in${cls}" x="${bx}" y="${f1(y2(0) - hIn)}" width="${f1(w)}" height="${f1(hIn)}" rx="2"${fill}/>`
                    + `<rect class="trend-line__bar trend-line__bar--out${cls}" x="${bx}" y="${f1(y2(0) + 1)}" width="${f1(w)}" height="${f1(Math.max(0, hOut - 1))}" rx="2"${fill}/>`;
            }
            if (bar.hatched) b += `<text class="trend-line__miss" x="${f1(g.x(k))}" y="${T - 4}" text-anchor="middle">⧗</text>`;
            if (bar.so_far) b += `<text class="trend-line__so-far" x="${f1(g.x(k))}" y="${T - 4}" text-anchor="middle">so far</text>`;
        });
        b += xLabels(m.months, g, H2 - B + 15, phone);
        m.months.forEach((x, k) => { b += `<rect class="trend-line__hit" data-k="${k}" data-bar="1" x="${f1(g.L + k * g.bw)}" y="${T}" width="${f1(g.bw)}" height="${H2 - T - B}"/>`; });
        const bottom = `<svg class="trend-line__svg" viewBox="0 0 ${width} ${H2}" height="${H2}" role="img" aria-label="Money in and money out per month">${b}</svg>`;
        return `<div class="trend-line trend-line--balance">`
            + `<p class="trend-line__title">Balance at each month end</p><div class="trend-line__chart">${top}<div class="trend-line__tip" hidden></div></div>${notes}`
            + `<p class="trend-line__title">Money in and money out</p>`
            + `<div class="trend-line__legend"><span class="trend-line__key trend-line__key--in"><i></i>Money in</span><span class="trend-line__key trend-line__key--out"><i></i>Money out</span><span class="trend-line__sub">Every row, transfers included</span></div>`
            + `<div class="trend-line__chart">${bottom}<div class="trend-line__tip" hidden></div></div>`
            + (m.note ? `<p class="trend-line__note">${esc(m.note)}</p>` : '') + `</div>`;
    }

    function typesSvg(m, width) {
        const phone = width < 520, n = m.months.length, idx = {};
        m.months.forEach((x, k) => { idx[x] = k; });
        const shown = m.lines.filter(l => !l.off);
        const coloured = l => l.colour !== 'grey';
        const g = geometry(width, n, phone, phone ? 10 : 120), T = 16, B = 24, H = phone ? 230 : 290;
        const sc = m.scale, y = v => T + (sc.hi - v) / (sc.hi - sc.lo) * (H - T - B), id = 'tl' + (++uid);
        let s = `<defs>${hatchDef(id)}</defs>`;
        ticks(sc).forEach(v => { s += `<line class="${v === 0 ? 'trend-line__base' : 'trend-line__grid'}" x1="${g.L}" x2="${f1(width - g.R)}" y1="${f1(y(v))}" y2="${f1(y(v))}"/>`
            + `<text class="trend-line__y" x="${g.L - 6}" y="${f1(y(v) + 4)}" text-anchor="end">${esc(v === 0 ? '0' : short(v, m.cur))}</text>`; });
        m.missing.forEach(x => { const k = idx[x];
            s += `<rect x="${f1(g.L + k * g.bw + 2)}" y="${T}" width="${f1(Math.max(1, g.bw - 4))}" height="${H - T - B}" rx="3" fill="url(#${id})"/>`
                + `<text class="trend-line__miss" x="${f1(g.x(k))}" y="${T - 4}" text-anchor="middle">⧗</text>`; });
        // Grey lines first, coloured above them, the picked line last of all.
        const order = shown.filter(l => !coloured(l) && !l.picked).concat(shown.filter(l => coloured(l) && !l.picked), shown.filter(l => l.picked));
        const labelled = [];
        order.forEach(l => {
            const values = l.points.map(p => p.value);
            const cls = `trend-line__series trend-line--${l.colour}${l.dim ? ' trend-line__series--dim' : ''}${l.picked ? ' trend-line__series--picked' : ''}`;
            let p = '';
            l.solid.forEach(run => {
                p += run.length > 1 ? `<path class="trend-line__ln" d="${pathOf(run, idx, g, y, values)}"/>`
                    : `<circle class="trend-line__dot" cx="${f1(g.x(idx[run[0]]))}" cy="${f1(y(values[idx[run[0]]]))}" r="2.5"/>`;
            });
            l.dashed.forEach(run => { p += `<path class="trend-line__ln trend-line__ln--est" d="${pathOf(run, idx, g, y, values)}"/>`; });
            l.points.forEach((pt, k) => { if (pt.estimate) p += `<circle class="trend-line__est" cx="${f1(g.x(k))}" cy="${f1(y(pt.value))}" r="${coloured(l) ? 4 : 3}"/>`; });
            const lastK = values.map((v, k) => v === null ? -1 : k).filter(k => k >= 0).pop();
            if (lastK !== undefined && (coloured(l) || l.picked) && !l.dim) {
                p += `<circle class="trend-line__dot" cx="${f1(g.x(lastK))}" cy="${f1(y(values[lastK]))}" r="4"/>`;
                if (!phone) {
                    // End labels never sit on each other: a label too close to one drawn is nudged down.
                    let ly = y(values[lastK]);
                    labelled.slice().sort((a, b) => a - b).forEach(o => { if (Math.abs(o - ly) < 13) ly = o + 13; });
                    labelled.push(ly);
                    p += `<text class="trend-line__end" x="${f1(g.x(lastK) + 9)}" y="${f1(ly + 4)}">${esc(l.name)}</text>`;
                }
            }
            s += `<g class="${cls}">${p}</g>`;
        });
        shown.forEach(l => {
            const values = l.points.map(p => p.value);
            const d = l.solid.concat(l.dashed).filter(r => r.length > 1).map(run => pathOf(run, idx, g, y, values)).join('');
            if (d) s += `<path class="trend-line__ln-hit" data-id="${esc(l.id)}" d="${d}"><title>${esc(l.name)} · ${esc(l.net_text)}</title></path>`;
        });
        s += xLabels(m.months, g, H - B + 15, phone);
        const chips = m.lines.map(l => `<button type="button" class="trend-line__chip trend-line--${l.slot ? 's' + l.slot : 'grey'}${l.picked ? ' is-picked' : ''}" data-id="${esc(l.id)}" aria-pressed="${!l.off}"><i></i>${esc(l.name)} <b>${esc(l.net_text)}</b></button>`).join('')
            + (m.any_off ? `<button type="button" class="trend-line__all" data-all="1">Show every line</button>` : '');
        return `<div class="trend-line trend-line--types"><div class="trend-line__legend" role="group" aria-label="Lines">${chips}</div>`
            + `<div class="trend-line__chart"><svg class="trend-line__svg" viewBox="0 0 ${width} ${H}" height="${H}" role="img" aria-label="Net spending per type over time">${s}</svg></div>`
            + (m.missing.length ? `<p class="trend-line__note">⧗ ${esc(m.missing.map(full).join(', '))}: no statement in yet on a card this book uses. A hollow dot is an estimate between its neighbours, drawn only, never counted.</p>` : '')
            + `</div>`;
    }

    /** The markup for a model at a width, as a string. */
    Trend.lineSvg = function lineSvg(m, width) {
        width = Math.max(280, Math.round(width || 0));
        return m.kind === 'types' ? typesSvg(m, width) : balanceSvg(m, width);
    };

    function tipHtml(m, k) {
        const month = m.months[k], p = m.points[k], bar = m.bars[k], cur = m.cur, rows = [];
        // As the card words figures: whole rupees without paise ("₹ 5,20,000"); a rate's
        // day as "30 Sep 2026", as the bars' hover names it.
        const amt = v => { const t = money(v, cur); return cur === 'INR' && Number.isInteger(v) && v % 100 === 0 ? t.replace(/\.00$/, '') : t; };
        const dayOf = d => (/^\d{4}-\d{2}-\d{2}$/.test(d || '') ? `${Number(d.slice(8, 10))} ${MON[Number(d.slice(5, 7)) - 1]} ${d.slice(0, 4)}` : (d || ''));
        if (bar.hatched) rows.push(`<div class="trend-line__tip-n">⧗ ${esc(MISSING_TEXT)}</div>`);
        if (m.empty) rows.push(`<div>${esc(m.empty)}</div>`);
        else if (p.value !== null && p.estimate) rows.push(`<div>Estimate ${esc(amt(p.value))}, between its neighbours; never counted</div>`);
        else if (p.value !== null) rows.push(`<div>Balance at month end <b>${esc(amt(p.value))}</b></div>`);
        else if (bar.note) rows.push(`<div>${esc(bar.note)}</div>`);
        else if (bar.so_far) rows.push('<div>No month end yet: so far</div>');
        rows.push(`<div>Money in <b>${esc(amt(bar.in))}</b></div><div>Money out <b>${esc(amt(bar.out))}</b></div>`);
        if (bar.rate && bar.rate.text) rows.push(`<div class="trend-line__tip-n">Rate ${esc(bar.rate.text)} (${esc(dayOf(bar.rate.date))})</div>`);
        return `<div class="trend-line__tip-h">${esc(full(month))}${bar.so_far ? ' · so far' : ''}</div>${rows.join('')}`;
    }

    /** A month's hover on the balance shape, as markup (for tests). */
    Trend.lineTip = tipHtml;

    /** The mode: draw(el, payload, opts) → {pick(id), toggle(id), showAll(), redraw()}.
     *  opts: {off, picked, onChange(state), onMonth(months, label), onImport(month)}; months is a list, as the bars send it. */
    function draw(el, payload, opts) {
        opts = opts || {};
        const state = { off: new Set(opts.off || []), picked: opts.picked === undefined ? null : opts.picked };
        let lastWidth = 0;
        function innerWidth() {
            const cs = typeof getComputedStyle === 'function' ? getComputedStyle(el) : null;
            const pad = cs ? (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0) : 0;
            return Math.max(0, (el.clientWidth || 0) - pad);
        }
        function idOf(v) { const n = Number(v); return Number.isNaN(n) ? v : n; }
        function changed() { if (opts.onChange) opts.onChange({ off: [...state.off], picked: state.picked }); render(); }
        function render() {
            const m = Trend.lineModel(payload, { off: [...state.off], picked: state.picked });
            lastWidth = innerWidth();
            el.innerHTML = Trend.lineSvg(m, lastWidth || 1366);
            if (m.kind === 'types') {
                el.querySelectorAll('.trend-line__chip').forEach(b => b.addEventListener('click', () => {
                    const id = idOf(b.dataset.id);
                    if (state.off.has(id)) state.off.delete(id); else state.off.add(id);
                    changed();
                }));
                const all = el.querySelector('.trend-line__all');
                if (all) all.addEventListener('click', () => { state.off.clear(); changed(); });
                el.querySelectorAll('.trend-line__ln-hit').forEach(p => p.addEventListener('click', () => {
                    const id = idOf(p.dataset.id);
                    state.picked = state.picked === id ? null : id;
                    changed();
                }));
                return;
            }
            el.querySelectorAll('.trend-line__chart').forEach(chart => {
                const tip = chart.querySelector('.trend-line__tip');
                chart.querySelectorAll('.trend-line__hit').forEach(r => {
                    const k = Number(r.dataset.k);
                    r.addEventListener('mouseenter', () => {
                        tip.innerHTML = tipHtml(m, k);
                        tip.hidden = false;
                        const box = chart.getBoundingClientRect(), rb = r.getBoundingClientRect();
                        const left = rb.left - box.left + rb.width / 2 - tip.offsetWidth / 2;
                        tip.style.left = Math.max(0, Math.min(left, box.width - tip.offsetWidth)) + 'px';
                    });
                    r.addEventListener('mouseleave', () => { tip.hidden = true; });
                    if (r.dataset.bar) r.addEventListener('click', () => {
                        const bar = m.bars[k];
                        if (bar.hatched) { if (opts.onImport) opts.onImport(bar.month); }
                        else if (opts.onMonth) opts.onMonth([bar.month], Trend.bars && Trend.bars.monthsLabel ? Trend.bars.monthsLabel([bar.month]) : bar.month);
                    });
                });
            });
        }
        render();
        if (typeof ResizeObserver !== 'undefined') {
            const ro = new ResizeObserver(() => { if (Math.abs(innerWidth() - lastWidth) > 1) render(); });
            ro.observe(el);
        }
        return {
            pick(id) { state.picked = id; changed(); },
            toggle(id) { if (state.off.has(id)) state.off.delete(id); else state.off.add(id); changed(); },
            showAll() { state.off.clear(); changed(); },
            redraw: render,
        };
    }

    if (typeof Trend.registerMode === 'function') Trend.registerMode('line', draw);
})();
