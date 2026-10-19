'use strict';
// Every card with a heading folds from that heading. Folded, the heading shows one line of numbers
// instead of the bars and charts: the section's data-sum, which the page scripts keep up to date.
// Open or closed is remembered per panel in this browser. Cards built later call KKFold.setup(section).
(function () {
    const KEY = 'rx-fold:' + location.pathname + ':';

    function remembered(id) {
        try { return localStorage.getItem(KEY + id); } catch (e) { return null; }
    }
    function remember(id, open) {
        try { localStorage.setItem(KEY + id, open ? '1' : '0'); } catch (e) { /* not remembered */ }
    }

    function set(sec, open, quiet) {
        sec.classList.toggle('folded', !open);
        const btn = sec.querySelector(':scope > h2 > .fold-toggle');
        if (btn) btn.setAttribute('aria-expanded', String(open));
        if (!quiet) remember(sec.id, open);
        if (open) window.dispatchEvent(new Event('resize'));  // charts drawn while hidden need their size
    }

    function setup(sec) {
        const h2 = sec.querySelector(':scope > h2');
        if (!h2 || sec.dataset.fold) return;
        sec.dataset.fold = '1';
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'fold-toggle';
        const chev = document.createElement('span');
        chev.className = 'fold-chev';
        chev.setAttribute('aria-hidden', 'true');
        chev.textContent = '▸';
        const label = document.createElement('span');
        label.className = 'fold-label';
        label.textContent = h2.textContent.trim();
        const sum = document.createElement('span');
        sum.className = 'fold-sum';
        btn.append(chev, label, sum);
        h2.replaceChildren(btn);
        btn.addEventListener('click', () => set(sec, sec.classList.contains('folded')));
        const show = () => { sum.textContent = sec.dataset.sum || ''; };
        new MutationObserver(show).observe(sec, { attributes: true, attributeFilter: ['data-sum'] });
        show();
        set(sec, remembered(sec.id) !== '0', true);
    }

    window.KKFold = { setup };
    function init() { document.querySelectorAll('main section.card[id]').forEach(setup); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
