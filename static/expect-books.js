// Expect back (personal-ledger 23): Books' three sections (D5), in the booksSections slot.
//
// personal-ledger 31: Expected back, Waiting for its row and To pay back, drawn from the sheet's
// `expect` payload (books_expect.py; tests/fixtures/expect/books_sections.json) between the sheet
// card and the month check. Each item opens its expectation's page (#/books/expectation/<id>);
// a rupee item shows ₹ with S$ beside, or "no rate". Every figure comes from the server's
// payload, never a formula here: totals, ages and "old" are the server's.
(function () {
    'use strict';

    const Expect = window.Expect;

    const SUBS = {
        expected_back: 'Yours, out with others: oldest first. Age only informs; nothing is let go by itself.',
        waiting: 'Money in marked “Back for…” before the money out arrived.',
        to_pay_back: 'Owed by you: money in marked “Expect to pay back”.',
    };

    function titleHTML(line) {
        const text = esc(line.title);
        return line.expectation_id
            ? `<a class="link xb-books__title" href="#/books/expectation/${Number(line.expectation_id)}">${text}</a>`
            : `<b class="xb-books__title">${text}</b>`;
    }

    function subHTML(line, section) {
        const bits = [esc(day(line.date))];
        if (line.note) bits.push(`“${esc(line.note)}”`);
        if (line.last_set_off) {
            bits.push(`${section.key === 'to_pay_back' ? 'last paid back' : 'last back'} ${esc(day(line.last_set_off))}`);
        }
        return `<div class="xb-books__sub">${bits.join(' · ')}</div>`;
    }

    function openHTML(line) {
        const own = esc(money(line.open_minor, line.currency));
        if (line.currency === 'SGD') return own;
        const beside = line.no_rate ? 'no rate' : esc(money(line.sgd_minor, 'SGD'));
        return `${own}<div class="xb-books__sub">${beside}</div>`;
    }

    function rowHTML(line, section) {
        return `<tr data-key="${esc(line.key)}">
            <td>${titleHTML(line)}${subHTML(line, section)}</td>
            <td class="xb-books__who">${esc((line.names || []).join(', '))}</td>
            <td class="r num">${openHTML(line)}</td>
            <td class="r num xb-books__who">${esc(money(line.amount_minor, line.currency))}</td>
            <td class="r"><span class="xb-age${line.old ? ' xb-old' : ''}">${Number(line.age_days)} d</span></td></tr>`;
    }

    function leftOutHTML(section) {
        const n = (section.left_out || []).length;
        if (!n) return '';
        const items = section.left_out.map(x => `${esc(x.title)} ${esc(money(x.open_minor, x.currency))}`).join('; ');
        return `<p class="card-foot">${plural(n, 'item')} left out of the S$ total (${esc(section.left_out[0].reason)}): ${items}</p>`;
    }

    /** A section's sortable columns (app.js's sortRows): money by its S$ figure, a line with none last. */
    function sortCols(section) {
        return {
            date: { label: section.owed ? 'Came in' : 'Paid', kind: 'date', of: l => l.date },
            who: { label: section.key === 'to_pay_back' ? 'To' : 'From', kind: 'text', of: l => (l.names || []).join(', ') },
            open: { label: 'Open', kind: 'money', of: l => l.currency === 'SGD' ? l.open_minor : l.sgd_minor },
            expected: { label: 'Expected', kind: 'money', of: l => l.currency === 'SGD' ? l.amount_minor : null },
            age: { label: 'Age', kind: 'number', of: l => l.age_days },
        };
    }

    function sectionHTML(section) {
        const owed = section.owed ? ' (owed)' : '';
        const out = section.in_total ? '' : ' <span class="tag xb-books__out">not in the total yet</span>';
        const table = `xb-${section.key}`, cols = sortCols(section), head = k => sortHead(table, k, cols);
        return `<section class="card xb-books" data-section="${esc(section.key)}">
        <div class="section-header"><div><h2>${esc(section.heading)}${owed}</h2><p>${esc(SUBS[section.key] || '')}</p></div>
            <span class="num xb-books__total">${esc(money(section.total_minor, 'SGD'))}${out}</span></div>
        <div class="table-shell"><table class="t"><thead><tr><th>${head('date')}</th><th class="xb-books__who">${head('who')}</th>
            <th class="r">${head('open')}</th><th class="r xb-books__who">${head('expected')}</th><th class="r">${head('age')}</th></tr></thead>
            <tbody>${sortRows(table, section.lines, cols).map(line => rowHTML(line, section)).join('')}</tbody></table></div>
        ${leftOutHTML(section)}</section>`;
    }

    /** The three sections of the sheet's `expect` payload, or '' when it holds none. */
    function booksSectionsHTML(sheet) {
        const sections = sheet && sheet.expect && sheet.expect.sections;
        if (!sections || !sections.length) return '';
        return `<div class="xb-books-all">${sections.map(sectionHTML).join('')}</div>`;
    }

    Expect.booksSectionsHTML = booksSectionsHTML;
    Expect.fill('booksSections', booksSectionsHTML);
})();
