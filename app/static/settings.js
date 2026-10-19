'use strict';
// Settings dialog, "Rescan library", per-series "Rescan" and the last-scan line.
// Settings are stored by the service (GET/POST /api/settings) and apply at once.
(function () {
  const FIELDS = [
    { name: 'mode', label: 'Mode', type: 'select', options: [
      ['report', 'Report only: measure, never encode'],
      ['manual', 'Manual: only the series you tick'],
      ['stage', 'Automatic: encode every series that qualifies'],
    ], help: 'Originals always stay until you press Confirm and Finalize.' },
    { name: 'parallel', label: 'Encodes at once', type: 'number', min: 1, max: 5, step: 1,
      help: 'More than 2 rarely helps: the GPU has two encoders and the quality check uses the CPU.' },
    { name: 'rescan_s', label: 'Look for new episodes every', type: 'select', options: [
      ['21600', '6 hours'], ['43200', '12 hours'], ['86400', 'day'], ['604800', 'week'],
    ], help: 'New episodes of ticked series (or of everything in Automatic) are encoded by themselves.' },
    { name: 'check_every_episode', label: 'Check every episode', type: 'checkbox',
      help: 'Off: after the first two episodes a series keeps its quality and skips the CPU check, so it is mostly GPU work.' },
    { name: 'target_vmaf', label: 'Quality target (VMAF)', type: 'number', min: 80, max: 100, step: 0.5,
      help: 'Higher is closer to the original; 95 looks the same to most people.' },
    { name: 'max_ratio', label: 'Keep a re-encode only up to', type: 'select', options: [
      ['0.6', '60% of the original size'], ['0.7', '70% of the original size'],
      ['0.8', '80% of the original size'], ['0.9', '90% of the original size'],
    ], help: 'Files that would not get smaller than this are left alone.' },
  ];

  let dialog, form, errorBox, loaded = {};

  async function api(path, body) {
    const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(path, Object.assign({ cache: 'no-store' }, opts));
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    return data;
  }

  function el(tag, props, ...children) {
    const e = document.createElement(tag);
    Object.assign(e, props || {});
    children.forEach(c => e.append(c));
    return e;
  }

  function buildDialog() {
    dialog = el('dialog', { id: 'settings', className: 'settings-dialog' });
    dialog.setAttribute('aria-labelledby', 'settings-title');
    form = el('form', { method: 'dialog' });
    form.append(el('h3', { id: 'settings-title', textContent: 'Settings' }));
    FIELDS.forEach(f => {
      const id = 'set-' + f.name;
      let input;
      if (f.type === 'select') {
        input = el('select', { id, name: f.name });
        f.options.forEach(([v, t]) => input.append(el('option', { value: v, textContent: t })));
      } else if (f.type === 'checkbox') {
        input = el('input', { id, name: f.name, type: 'checkbox' });
      } else {
        input = el('input', { id, name: f.name, type: 'number', min: f.min, max: f.max, step: f.step, inputMode: 'decimal' });
      }
      const label = el('label', { htmlFor: id, textContent: f.label });
      const row = el('div', { className: 'set-row' + (f.type === 'checkbox' ? ' set-check' : '') });
      if (f.type === 'checkbox') row.append(input, label); else row.append(label, input);
      row.append(el('p', { className: 'set-help', textContent: f.help }));
      form.append(row);
    });
    errorBox = el('p', { className: 'set-error', hidden: true });
    errorBox.setAttribute('role', 'alert');
    const save = el('button', { type: 'submit', className: 'btn btn-primary', textContent: 'Save' });
    const cancel = el('button', { type: 'button', className: 'btn', textContent: 'Cancel' });
    cancel.addEventListener('click', () => dialog.close());
    form.append(errorBox, el('div', { className: 'set-actions' }, cancel, save));
    form.addEventListener('submit', onSave);
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    dialog.append(form);
    document.body.append(dialog);
  }

  function value(f) {
    const input = form.elements[f.name];
    if (f.type === 'checkbox') return input.checked;
    if (f.name === 'mode') return input.value;
    return Number(input.value);
  }

  async function open() {
    errorBox.hidden = true;
    try {
      loaded = await api('/api/settings');
      FIELDS.forEach(f => {
        const input = form.elements[f.name];
        if (f.type === 'checkbox') input.checked = !!loaded[f.name];
        else input.value = String(loaded[f.name]);
      });
    } catch (e) {
      errorBox.textContent = 'Could not load the settings: ' + e.message;
      errorBox.hidden = false;
    }
    if (!dialog.open) dialog.showModal();
  }

  async function onSave(e) {
    e.preventDefault();
    const changes = {};
    FIELDS.forEach(f => {
      const v = value(f);
      if (String(v) !== String(loaded[f.name])) changes[f.name] = v;
    });
    if (!Object.keys(changes).length) { dialog.close(); return; }
    try {
      loaded = await api('/api/settings', changes);
      dialog.close();
      window.dispatchEvent(new CustomEvent('kk-settings-saved', { detail: loaded }));
    } catch (err) {
      errorBox.textContent = err.message;
      errorBox.hidden = false;
    }
  }

  function busy(btn, text) {
    const old = btn.textContent;
    btn.textContent = text;
    btn.disabled = true;
    setTimeout(() => { btn.textContent = old; btn.disabled = false; }, 3000);
  }

  async function updateLastScan(line) {
    try {
      const s = await api('/api/status');
      const when = s.last_scan ? new Date(s.last_scan).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'not yet';
      line.textContent = 'Last scan: ' + when + (s.scanning ? ' · scanning…' : '');
    } catch (e) { /* the main page shows the connection state */ }
  }

  function init() {
    buildDialog();
    const nav = document.querySelector('header.top nav.controls');
    const gear = el('button', { id: 'open-settings', type: 'button', className: 'btn', textContent: 'Settings' });
    gear.setAttribute('aria-haspopup', 'dialog');
    gear.addEventListener('click', open);
    if (nav) nav.prepend(gear);

    const toolbar = document.querySelector('nav.toolbar');
    const rescan = el('button', { id: 'rescan-all', type: 'button', className: 'btn', textContent: 'Rescan library' });
    rescan.addEventListener('click', async () => {
      busy(rescan, 'Rescanning…');
      try { await api('/api/scan', {}); } catch (e) { /* shown by the last-scan line */ }
    });
    const line = el('span', { id: 'last-scan', className: 'muted' });
    if (toolbar) toolbar.append(rescan, line);
    updateLastScan(line);
    setInterval(() => updateLastScan(line), 10000);

    // per-series Rescan buttons (cards are rebuilt by app.js, so listen on the list)
    const list = document.getElementById('folders');
    if (list) list.addEventListener('click', async e => {
      const btn = e.target.closest('.btn-rescan');
      if (!btn) return;
      e.stopPropagation();
      busy(btn, 'Rescanning…');
      try { await api('/api/scan', { library: btn.dataset.library, folder: btn.dataset.folder }); } catch (err) { /* ignore */ }
    }, true);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
