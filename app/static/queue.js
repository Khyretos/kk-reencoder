'use strict';
// The queue: which series the encoder takes next, in order. Drag a row by its grip (mouse or finger),
// press ↑ to send it to the front, or ✕ to take it out (it is paused and listed under "Taken out").
// Reordering sets the series' priorities on the server, like the CI queue page does for jobs.
(function () {
  const SHOW = 30;   // rows per group before "Show all"
  let latest = null, dragging = null, expanded = {};

  function fmt(b) {
    if (!b || b < 1) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(4, Math.floor(Math.log(b) / Math.log(1024)));
    return (b / Math.pow(1024, i)).toFixed(i >= 3 ? 1 : 0) + ' ' + u[i];
  }
  const title = n => (n || '').split('/').pop();
  const key = it => it.library + '/' + it.name;
  async function post(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }
  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function build() {
    const sec = h('section', 'card');
    sec.id = 'queue';
    const head = h('h2', null, 'Queue');
    const body = h('div', 'q-groups');
    const hint = h('p', 'hint q-hint', 'Drag a row by its grip, press ↑ to send it to the front, ✕ to take it out');
    sec.append(head, hint, body);
    const jobs = document.getElementById('jobs');
    (jobs || document.getElementById('folders')).after(sec);
    sec.addEventListener('click', onClick);
    sec.addEventListener('pointerdown', onDown);
    if (window.KKFold) KKFold.setup(sec);  // folds from its heading like the other panels
    return sec;
  }

  function row(it, n) {
    const li = h('li', 'q-row' + (it.running ? ' running' : '') + (n === 1 ? ' first' : ''));
    li.dataset.key = key(it);
    li.dataset.library = it.library;
    li.dataset.folder = it.name;
    const grip = h('span', 'q-grip', '⠿');
    grip.setAttribute('aria-hidden', 'true');
    const num = h('span', 'q-num', String(n));
    const body = h('div', 'q-body');
    body.append(h('span', 'q-name', title(it.name)),
      h('span', 'q-left', `${it.left} of ${it.files} file${it.files === 1 ? '' : 's'} left`),
      h('span', 'q-size', it.running ? 'working on it now' : `~${fmt(it.left_bytes)}`));
    const chips = h('span', 'q-chips');
    chips.append(h('span', 'q-chip q-lib', it.library.toLowerCase()),
      h('span', 'q-chip ' + (it.action === 'encode' ? 'q-enc' : 'q-mea'), it.action === 'encode' ? 'AV1 encode' : 'estimate'));
    const top = h('button', 'q-top', '↑');
    top.type = 'button';
    top.title = 'Send to the front';
    top.setAttribute('aria-label', 'Send ' + title(it.name) + ' to the front');
    top.disabled = n === 1;
    const out = h('button', 'q-out', '✕');
    out.type = 'button';
    out.title = 'Take out of the queue (pauses this series)';
    out.setAttribute('aria-label', 'Take ' + title(it.name) + ' out of the queue');
    li.append(grip, num, body, chips, top, out);
    return li;
  }

  function group(label, hint, items, gid) {
    const wrap = h('div', 'q-group q-g-' + gid);
    const hd = h('h3', null, label + ' ');
    hd.append(h('span', 'q-count', String(items.length)));
    if (hint) hd.append(h('span', 'hint', ' ' + hint));
    const ol = h('ol', 'q-list');
    ol.dataset.group = gid;
    const shown = expanded[gid] ? items : items.slice(0, SHOW);
    shown.forEach((it, i) => ol.append(row(it, i + 1)));
    wrap.append(hd, ol);
    if (items.length > SHOW) {
      const more = h('button', 'btn q-more', expanded[gid] ? 'Show fewer' : `Show all ${items.length}`);
      more.type = 'button';
      more.dataset.more = gid;
      wrap.append(more);
    }
    if (!items.length) wrap.append(h('p', 'muted', 'Nothing here.'));
    return wrap;
  }

  function paint() {
    if (!latest || dragging) return;
    const sec = document.getElementById('queue') || build();
    const enc = latest.items.filter(i => i.action === 'encode');
    const mea = latest.items.filter(i => i.action === 'measure');
    const groups = sec.querySelector('.q-groups');
    const next = enc[0] || mea[0];
    sec.dataset.sum = `${enc.length} to encode · ${mea.length} to measure` +
      (latest.paused.length ? ` · ${latest.paused.length} taken out` : '') + (next ? ` · next: ${title(next.name)}` : '');
    const scroll = window.scrollY;
    groups.replaceChildren(
      group('Encoding next', null, enc, 'encode'),
      group('Measuring next', 'estimates only, nothing is written', mea, 'measure'));
    if (latest.paused.length) {
      const det = h('details', 'q-paused');
      det.open = !!expanded.paused;
      det.addEventListener('toggle', () => { expanded.paused = det.open; });
      det.append(h('summary', null, `Taken out (${latest.paused.length})`));
      const ul = h('ul', 'q-list');
      latest.paused.forEach(it => {
        const li = h('li', 'q-row');
        li.dataset.library = it.library;
        li.dataset.folder = it.name;
        const body = h('div', 'q-body');
        body.append(h('span', 'q-name', title(it.name)), h('span', 'q-left', `${it.left} files left`), h('span', 'q-size', `~${fmt(it.left_bytes)}`));
        const back = h('button', 'btn q-back', 'Put back');
        back.type = 'button';
        li.append(body, back);
        ul.append(li);
      });
      det.append(ul);
      groups.append(det);
    }
    window.scrollTo(0, scroll);
  }

  async function load() {
    try {
      const r = await fetch('/api/queue', { cache: 'no-store' });
      if (r.ok) { latest = await r.json(); paint(); }
    } catch (e) { /* the main page shows the connection state */ }
  }

  function orderNow(list) {
    // only the group that changed: reordering what gets encoded leaves the measuring order alone
    return [...(list || document.getElementById('queue')).querySelectorAll('.q-row')].map(li => ({ library: li.dataset.library, folder: li.dataset.folder }));
  }
  async function sendOrder(order) {
    try { await post('/api/queue', { order }); } catch (e) { /* refreshed below */ }
    load();
  }

  async function onClick(e) {
    const more = e.target.closest('[data-more]');
    if (more) { expanded[more.dataset.more] = !expanded[more.dataset.more]; paint(); return; }
    const li = e.target.closest('.q-row');
    if (!li) return;
    const target = { library: li.dataset.library, folder: li.dataset.folder };
    if (e.target.closest('.q-top')) {
      const list = li.parentElement;
      list.prepend(li);
      sendOrder(orderNow(list));
    } else if (e.target.closest('.q-out')) {
      li.remove();
      await post('/api/folder', Object.assign({ paused: true }, target)).catch(() => {});
      load();
    } else if (e.target.closest('.q-back')) {
      await post('/api/folder', Object.assign({ paused: false }, target)).catch(() => {});
      load();
    }
  }

  // drag by the grip, within one group (pointer events: mouse, pen and touch)
  function onDown(e) {
    const grip = e.target.closest('.q-grip');
    if (!grip || (e.pointerType === 'mouse' && e.button !== 0)) return;
    const li = grip.closest('.q-row');
    e.preventDefault();
    dragging = { li, list: li.parentElement, start: orderNow(li.parentElement).map(o => o.library + '/' + o.folder).join('|'), id: e.pointerId };
    li.classList.add('dragging');
    // listen on window: moving the row in the DOM drops pointer capture on the grip
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  }
  function onMove(e) {
    if (!dragging || e.pointerId !== dragging.id) return;
    const over = document.elementFromPoint(e.clientX, e.clientY);
    const target = over && over.closest('.q-row');
    if (!target || target === dragging.li || target.parentElement !== dragging.list) return;
    const r = target.getBoundingClientRect();
    dragging.list.insertBefore(dragging.li, e.clientY > r.top + r.height / 2 ? target.nextSibling : target);
  }
  function onUp(e) {
    if (!dragging || e.pointerId !== dragging.id) return;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    const d = dragging;
    dragging = null;
    d.li.classList.remove('dragging');
    d.list.querySelectorAll('.q-row').forEach((r, i) => {
      r.querySelector('.q-num').textContent = String(i + 1);
      r.classList.toggle('first', i === 0);
      r.querySelector('.q-top').disabled = i === 0;
    });
    const now = orderNow(d.list);
    if (now.map(o => o.library + '/' + o.folder).join('|') !== d.start) sendOrder(now);
  }

  function init() {
    load();
    setInterval(load, 5000);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
