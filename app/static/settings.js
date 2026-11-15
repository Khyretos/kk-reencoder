'use strict';
// Settings dialog, "Rescan library", per-series "Rescan" and the last-scan line.
// Settings are stored by the service (GET/POST /api/settings) and apply at once.
(function () {
  const CODECS = [
    ['h264', 'H.264 / AVC'], ['hevc', 'H.265 / HEVC'], ['mpeg4', 'MPEG-4 Part 2 (XviD/DivX)'], ['vc1', 'VC-1'],
    ['mpeg2video', 'MPEG-2'], ['wmv3', 'WMV 9'], ['msmpeg4v3', 'MS MPEG-4 v3'], ['vp9', 'VP9'], ['vp8', 'VP8'],
    ['mpeg1video', 'MPEG-1'], ['wmv2', 'WMV 8'], ['msmpeg4v2', 'MS MPEG-4 v2'],
  ];
  const GROUPS = [
    { title: 'General', fields: [
      { name: 'mode', label: 'Mode', type: 'select', options: [
        ['report', 'Report only: measure, never encode'],
        ['manual', 'Manual: only the series you tick'],
        ['stage', 'Automatic: encode every series that qualifies'],
      ], help: 'Originals always stay until you press Confirm and Finalize.' },
      { name: 'parallel', label: 'Encodes at once', type: 'number', min: 1, max: 5, step: 1,
        help: 'More than 2 rarely helps: the GPU has two encoders and the quality check uses the CPU.' },
      { name: 'rescan_s', label: 'Look for new episodes every', type: 'select', options: [
        ['3600', 'hour'], ['21600', '6 hours'], ['43200', '12 hours'], ['86400', 'day'], ['604800', 'week'],
      ], help: 'New episodes of ticked series (or of everything in Automatic) are encoded by themselves.' },
    ] },
    { title: 'Quality', fields: [
      { name: 'target_vmaf', label: 'Quality target (VMAF)', type: 'number', min: 80, max: 100, step: 0.5,
        help: 'Higher is closer to the original; 95 looks the same to most people.' },
      { name: 'max_ratio', label: 'Keep a re-encode only up to', type: 'select', options: [
        ['0.6', '60% of the original size'], ['0.7', '70% of the original size'],
        ['0.8', '80% of the original size'], ['0.9', '90% of the original size'],
        ['0.95', '95% of the original size (saves at least 5%)'], ['0.99', '99% (keep any saving)'],
      ], help: 'Files that would not get smaller than this are left alone. Changing it measures the files skipped as too big again.' },
      { name: 'check_every_episode', label: 'Check every episode', type: 'checkbox',
        help: 'Off: after the first episodes a series keeps its quality and skips the CPU check, so it is mostly GPU work.' },
      { name: 'samples', label: 'Samples per file', type: 'number', min: 1, max: 10, step: 1,
        help: 'How many short clips the quality search scores. More is more exact and slower.' },
      { name: 'sample_len', label: 'Sample length (seconds)', type: 'number', min: 5, max: 60, step: 1,
        help: 'Length of each scored clip.' },
      { name: 'search_per_folder', label: 'Full searches per series', type: 'number', min: 1, max: 10, step: 1,
        help: 'Episodes searched in full before the rest of a series starts from their quality.' },
    ] },
    { title: 'What gets re-encoded', fields: [
      { name: 'codecs', label: 'Source codecs', type: 'codecs',
        help: 'Only files in these codecs are re-encoded. Waiting files of a codec you untick are skipped at once; ticking one back in brings its files back with a scan.' },
      { name: 'max_height', label: 'Largest resolution', type: 'select', options: [
        ['720', '720p'], ['1080', '1080p'], ['1440', '1440p'], ['2160', '4K (2160p)'], ['4320', '8K (4320p)'],
      ], help: 'Taller videos are left alone.' },
      { name: 'skip_hdr', label: 'Skip HDR videos', type: 'checkbox',
        help: 'Off: HDR10 and HLG videos are re-encoded too and stay HDR. Dolby Vision drops to its HDR10 picture; Dolby Vision profile 5 is always left alone.' },
    ] },
    { title: 'Hardware and storage', fields: [
      { name: 'encoder_pref', label: 'Encoder', type: 'select', options: [
        ['auto', 'Auto: find the GPU and test it'], ['qsv', 'Intel Quick Sync (Arc, Core Ultra)'],
        ['vaapi', 'VA-API (AMD RX 7000+, Intel)'], ['nvenc', 'NVIDIA NVENC (RTX 40+)'], ['svt', 'CPU (SVT-AV1, slower)'],
      ], help: 'Auto tries the GPUs the container can see and falls back to the CPU. The next file uses a change; one already encoding finishes as it started.' },
      { name: 'hw_decode', label: 'Decode on the GPU', type: 'checkbox',
        help: 'Intel QSV only: decode and scale on the GPU too. A file that fails falls back to the CPU by itself.' },
      { name: 'vmaf_gpu', label: 'Score quality on the GPU', type: 'select', options: [
        ['auto', 'Auto: GPU when available and checked against the CPU'], ['off', 'Off: always score on the CPU'],
      ], help: 'Needs the kk-vmafx base image. Videos below 720p are always scored on the CPU.' },
      { name: 'max_staged_gb', label: 'Space for copies waiting for confirm (GB)', type: 'number', min: 1, max: 100000, step: 1,
        help: 'New encodes wait when copies waiting for Confirm and kept originals take more than this.' },
    ] },
  ];
  const FIELDS = GROUPS.flatMap(g => g.fields);

  let dialog, form, errorBox, about, loaded = {};

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

  function buildInput(f, id) {
    if (f.type === 'select') {
      const input = el('select', { id, name: f.name });
      f.options.forEach(([v, t]) => input.append(el('option', { value: v, textContent: t })));
      return input;
    }
    if (f.type === 'checkbox') return el('input', { id, name: f.name, type: 'checkbox', className: 'switch' });
    if (f.type === 'codecs') {
      const box = el('div', { id, className: 'set-codecs', role: 'group' });
      CODECS.forEach(([v, t]) => {
        const cb = el('input', { type: 'checkbox', value: v, name: 'codec' });
        box.append(el('label', { className: 'codec' }, cb, el('span', { textContent: t })));
      });
      return box;
    }
    return el('input', { id, name: f.name, type: 'number', min: f.min, max: f.max, step: f.step, inputMode: 'decimal' });
  }

  function buildDialog() {
    dialog = el('dialog', { id: 'settings', className: 'settings-dialog' });
    dialog.setAttribute('aria-labelledby', 'settings-title');
    form = el('form', { method: 'dialog' });
    const head = el('div', { className: 'set-head' }, el('div', { className: 'set-titles' },
      el('h3', { id: 'settings-title', textContent: 'Settings' }),
      el('p', { className: 'set-note', textContent: 'Saved in the service and used at once; they override the RX_* environment values.' })));
    const close = el('button', { type: 'button', className: 'btn set-x', textContent: '×' });
    close.setAttribute('aria-label', 'Close');
    close.addEventListener('click', () => dialog.close());
    head.append(close);
    form.append(head);
    const body = el('div', { className: 'set-body' });
    GROUPS.forEach(g => {
      const fs = el('fieldset', { className: 'set-group' }, el('legend', { textContent: g.title }));
      g.fields.forEach(f => {
        const id = 'set-' + f.name;
        const input = buildInput(f, id);
        const label = el(f.type === 'codecs' ? 'span' : 'label', { className: 'set-label', textContent: f.label });
        if (f.type !== 'codecs') label.htmlFor = id;
        const row = el('div', { className: 'set-row' + (f.type === 'checkbox' ? ' set-check' : '') + (f.type === 'codecs' ? ' set-wide' : '') });
        if (f.type === 'checkbox') row.append(el('div', { className: 'set-line' }, label, input));
        else row.append(label, input);
        row.append(el('p', { className: 'set-help', textContent: f.help }));
        fs.append(row);
      });
      body.append(fs);
    });
    about = el('fieldset', { className: 'set-group set-about' }, el('legend', { textContent: 'About this install' }));
    body.append(about);
    form.append(body);
    errorBox = el('p', { className: 'set-error', hidden: true });
    errorBox.setAttribute('role', 'alert');
    const save = el('button', { type: 'submit', className: 'btn btn-primary', textContent: 'Save changes' });
    const cancel = el('button', { type: 'button', className: 'btn', textContent: 'Cancel' });
    cancel.addEventListener('click', () => dialog.close());
    form.append(el('div', { className: 'set-actions' }, errorBox, cancel, save));
    form.addEventListener('submit', onSave);
    dialog.addEventListener('click', e => { if (e.target === dialog) dialog.close(); });
    dialog.append(form);
    document.body.append(dialog);
  }

  function value(f) {
    if (f.type === 'codecs') return [...form.querySelectorAll('input[name=codec]:checked')].map(c => c.value);
    const input = form.elements[f.name];
    if (f.type === 'checkbox') return input.checked;
    if (f.type === 'select' && !/^[0-9.]+$/.test(input.value)) return input.value;
    return Number(input.value);
  }

  function fillAbout(s) {
    const rows = [
      ['Version', s.version ? 'v' + s.version : '—'],
      ['Encoder in use', ((s.hardware && s.hardware.name) || s.encoder || '—') + (s.hardware && s.hardware.device ? ' · ' + s.hardware.device : '')],
      ['GPUs found', ((s.hardware && s.hardware.gpus) || []).map(g => g.vendor + (g.node ? ' (' + g.node.split('/').pop() + ')' : '')).join(', ') || 'none (CPU only)'],
      ['Quality scored on', { gpu: 'GPU (VMAFx SYCL)', cpu: 'CPU (libvmaf)', checking: 'GPU, checked at the first quality check' }[s.vmaf_on] || '—'],
      ['Libraries', (s.libraries || []).join(', ') || '—'],
    ];
    const dl = el('dl', { className: 'set-dl' });
    rows.forEach(([k, v]) => dl.append(el('dt', { textContent: k }), el('dd', { textContent: v })));
    about.replaceChildren(el('legend', { textContent: 'About this install' }), dl);
  }

  async function open() {
    errorBox.hidden = true;
    try {
      const [cur, status] = await Promise.all([api('/api/settings'), api('/api/status').catch(() => ({}))]);
      loaded = cur;
      FIELDS.forEach(f => {
        if (f.type === 'codecs') {
          const on = new Set(loaded.codecs || []);
          form.querySelectorAll('input[name=codec]').forEach(c => { c.checked = on.has(c.value); });
          return;
        }
        const input = form.elements[f.name];
        if (f.type === 'checkbox') { input.checked = !!loaded[f.name]; return; }
        const v = String(loaded[f.name]);
        if (f.type === 'select' && ![...input.options].some(o => o.value === v)) {
          // a value set through the environment that is not in the list: show it as it is
          input.append(el('option', { value: v, textContent: v }));
        }
        input.value = v;
      });
      fillAbout(status);
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
      if (f.type === 'codecs' ? v.join(',') !== (loaded.codecs || []).join(',') : String(v) !== String(loaded[f.name])) changes[f.name] = v;
    });
    if (!Object.keys(changes).length) { dialog.close(); return; }
    if (changes.codecs && !changes.codecs.length) {
      errorBox.textContent = 'Tick at least one source codec.';
      errorBox.hidden = false;
      return;
    }
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
    const gear = el('button', { id: 'open-settings', type: 'button', className: 'btn btn-gear', textContent: 'Settings' });
    gear.setAttribute('aria-haspopup', 'dialog');
    gear.addEventListener('click', open);
    if (nav) nav.append(gear);

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
