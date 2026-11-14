'use strict';
// The header every page shares: run state (Encoding / Paused / Idle / Offline), the Pause / Resume button,
// the mode badge and the version. app.js hands it each /api/status it polls (KKShell.apply); a page without
// app.js (the overview) polls on its own. While paused: the button turns into an amber Resume, the state dot
// turns amber, a banner says what pausing did, and the page gets body.is-paused for the rest of the styling.
(function () {
  const MODES = { report: 'Report only', stage: 'Automatic', manual: 'Manual: ticked series' };
  let last = null;
  let lastApply = 0;
  let busy = false;
  const baseTitle = document.title.replace(/^⏸ Paused · /, '');

  function $(id) { return document.getElementById(id); }

  function banner() {
    let b = $('pause-banner');
    if (b) return b;
    const main = document.querySelector('main');
    if (!main) return null;
    b = document.createElement('div');
    b.id = 'pause-banner';
    b.setAttribute('role', 'status');
    b.hidden = true;
    const icon = document.createElement('span');
    icon.className = 'pb-icon';
    icon.setAttribute('aria-hidden', 'true');
    const text = document.createElement('div');
    text.className = 'pb-text';
    const strong = document.createElement('strong');
    strong.textContent = 'Encoding is paused';
    const small = document.createElement('span');
    small.className = 'pb-sub';
    text.append(strong, small);
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn pb-resume';
    btn.textContent = 'Resume';
    btn.addEventListener('click', () => toggle(false));
    b.append(icon, text, btn);
    main.prepend(b);
    return b;
  }

  function setRun(cls, text, title) {
    const rs = $('runstate');
    if (!rs) return;
    rs.className = 'runstate ' + cls;
    rs.querySelector('.rs-text').textContent = text;
    rs.title = title || '';
  }

  function render(s) {
    const btn = $('pause');
    if (!s) {
      setRun('offline', 'Offline', 'The service does not answer');
      if (btn) btn.disabled = true;
      return;
    }
    const jobs = (s.jobs || []).length;
    const paused = !!s.paused;
    document.body.classList.toggle('is-paused', paused);
    document.title = (paused ? '⏸ Paused · ' : '') + baseTitle;
    if (btn) {
      btn.disabled = false;
      btn.classList.toggle('is-paused', paused);
      btn.setAttribute('aria-pressed', String(paused));
      btn.querySelector('.lbl').textContent = paused ? 'Resume' : 'Pause';
      btn.title = paused ? 'Resume encoding: frozen jobs carry on where they were' : 'Pause all encoding and quality checks';
    }
    if (paused) setRun('paused', jobs ? `Paused · ${jobs} frozen` : 'Paused', 'Nothing runs until you press Resume');
    else if (jobs) setRun('running', `Encoding · ${jobs}`, `${jobs} file${jobs === 1 ? '' : 's'} in progress`);
    else if (s.scanning) setRun('running', 'Scanning', 'Looking for new or changed files');
    else setRun('idle', 'Idle', 'Nothing to do right now');

    const mode = $('mode');
    if (mode) {
      mode.textContent = MODES[s.mode] || s.mode || '';
      mode.dataset.mode = s.mode || '';
    }
    const ver = $('ver');
    if (ver && s.version) ver.textContent = 'v' + s.version;

    const b = banner();
    if (b) {
      b.hidden = !paused;
      b.querySelector('.pb-sub').textContent = jobs
        ? `${jobs} running job${jobs === 1 ? ' is' : 's are'} frozen and will carry on where ${jobs === 1 ? 'it' : 'they'} stopped. No new files start.`
        : 'No new files start until you resume.';
    }
  }

  async function toggle(want) {
    if (busy) return;
    const paused = typeof want === 'boolean' ? want : !(last && last.paused);
    busy = true;
    // optimistic: show the new state at once, the answer below confirms it
    last = Object.assign({}, last || {}, { paused });
    render(last);
    try {
      const r = await fetch('/api/pause', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                            body: JSON.stringify({ paused }) });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      last = await r.json();
    } catch (e) {
      last = Object.assign({}, last, { paused: !paused });  // it did not happen: put the button back
    }
    busy = false;
    render(last);
    window.dispatchEvent(new CustomEvent('kk-status', { detail: last }));
  }

  async function poll() {
    if (Date.now() - lastApply < 5000) return;  // app.js polls and hands us its answers
    try {
      const r = await fetch('/api/status', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      last = await r.json();
      render(last);
    } catch (e) {
      render(null);
    }
  }

  window.KKShell = {
    apply(s) {
      lastApply = Date.now();
      if (busy) return;  // a toggle is under way: keep showing what was asked for
      if (s) last = s;
      render(s);
    },
    get status() { return last; },
  };

  function init() {
    const btn = $('pause');
    if (btn) btn.addEventListener('click', () => toggle());
    banner();
    poll();
    setInterval(poll, 2500);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
