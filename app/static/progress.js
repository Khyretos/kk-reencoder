'use strict';
// "Progress per library": per library (and for all of them) how many files are re-encoded out of the total,
// as a number, a percentage and one stacked bar: finalized, waiting for Confirm/Finalize, measured only,
// skipped, failed, still to do. Under it what the re-encoded files saved. Data: GET /api/libraries.
(function () {
  const SEGMENTS = [
    // key, label, how many files
    ['final', 'Finalized', l => l.encoded || 0],
    ['wait', 'Waiting for confirm / finalize', l => (l.staged || 0) + (l.confirmed || 0)],
    ['meas', 'Measured only', l => l.predicted || 0],
    ['skip', 'Skipped', l => l.skipped || 0],
    ['fail', 'Failed', l => l.failed || 0],
  ];

  function fmtBytes(b) {
    if (!b || b < 1) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = Math.min(4, Math.floor(Math.log(b) / Math.log(1024)));
    if (i < 4 && b / Math.pow(1024, i) >= 1000) i += 1;  // 0.98 TB, not 1000.9 GB
    return parseFloat((b / Math.pow(1024, i)).toFixed(1)) + ' ' + u[i];
  }
  function pct(part, whole) {
    if (!whole || !part) return '0%';
    const p = part / whole * 100;
    if (p >= 99.95 && part < whole) return '99.9%';
    return (p < 10 ? p.toFixed(1) : p >= 100 ? '100' : p.toFixed(0)) + '%';
  }
  function n(x) { return (x || 0).toLocaleString(); }

  function h(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function total(libs) {
    const t = { library: 'All libraries', folders: 0, all: true };
    const keys = ['files', 'pending', 'running', 'encoded', 'predicted', 'staged', 'confirmed', 'skipped', 'failed',
                  'original_bytes', 'reencoded', 'reencoded_before_bytes', 'reencoded_after_bytes', 'saved_bytes'];
    keys.forEach(k => { t[k] = libs.reduce((a, l) => a + (l[k] || 0), 0); });
    t.folders = libs.reduce((a, l) => a + (l.folders || 0), 0);
    return t;
  }

  function row(l) {
    const r = h('div', 'lp-row' + (l.all ? ' lp-total' : ''));
    const head = h('div', 'lp-head');
    const name = h('div', 'lp-name');
    name.append(h('strong', null, l.library),
                h('small', null, `${n(l.folders)} ${l.folders === 1 ? 'title' : 'titles'} · ${fmtBytes(l.original_bytes)}`));
    const big = h('div', 'lp-big');
    big.append(h('span', 'lp-pct', pct(l.reencoded, l.files)),
               h('span', 'lp-count', `${n(l.reencoded)} / ${n(l.files)} re-encoded`));
    head.append(name, big);

    const bar = h('div', 'lp-bar');
    bar.setAttribute('role', 'img');
    const parts = [];
    SEGMENTS.forEach(([key, label, get]) => {
      const c = get(l);
      if (!c || !l.files) return;
      const seg = h('i', 'seg-' + key);
      seg.style.width = (c / l.files * 100) + '%';
      seg.title = `${label}: ${n(c)} (${pct(c, l.files)})`;
      bar.append(seg);
      parts.push(`${label} ${n(c)}`);
    });
    const left = (l.pending || 0) + (l.running || 0);
    parts.push(`left ${n(left)}`);
    bar.setAttribute('aria-label', `${l.library}: ${parts.join(', ')}`);

    const foot = h('div', 'lp-foot');
    if (l.reencoded) {
      const saved = h('span', 'lp-saved');
      saved.append(h('b', null, fmtBytes(l.saved_bytes)), document.createTextNode(' saved'));
      foot.append(saved, h('span', null,
        `${fmtBytes(l.reencoded_before_bytes)} → ${fmtBytes(l.reencoded_after_bytes)} (−${pct(l.saved_bytes, l.reencoded_before_bytes)}) on those files`));
    } else {
      foot.append(h('span', 'lp-saved', 'nothing re-encoded yet'));
    }
    if (left) foot.append(h('span', null, `${n(left)} to do`));
    if (l.skipped) foot.append(h('span', null, `${n(l.skipped)} skipped`));
    if (l.failed) foot.append(h('span', 'lp-failed', `${n(l.failed)} failed`));
    r.append(head, bar, foot);
    return r;
  }

  function legend() {
    const lg = h('div', 'lp-legend');
    SEGMENTS.concat([['left', 'Still to do']]).forEach(([key, label]) => {
      const it = h('span', 'lp-key');
      it.append(h('i', 'seg-' + key), document.createTextNode(label));
      lg.append(it);
    });
    return lg;
  }

  window.KKProgress = {
    pct,
    render(el, libs) {
      if (!el) return;
      if (!libs || !libs.length) {
        el.replaceChildren(h('p', 'muted', 'No libraries scanned yet.'));
        return;
      }
      const rows = h('div', 'lp-rows');
      if (libs.length > 1) rows.append(row(total(libs)));
      [...libs].sort((a, b) => (b.original_bytes || 0) - (a.original_bytes || 0)).forEach(l => rows.append(row(l)));
      el.replaceChildren(rows, legend());
    },
  };
})();
