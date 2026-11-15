'use strict';
// Themes, chosen per browser (remembered in localStorage): System (follows the OS), Dark, Light (Kreative Kompas
// lilac) and Grey (a plain neutral light theme). The stylesheet keeps its light rules in
// @media (prefers-color-scheme: light) blocks; a chosen theme switches those blocks on ('all') or off ('not all'),
// so every page and every older rule follows without a second copy of the CSS. Grey adds its own variables on top
// (html[data-theme="grey"]). The picker is the #theme select in the header.
(function () {
  const KEY = 'rx-theme';
  const THEMES = [['system', 'System'], ['dark', 'Dark'], ['light', 'Light'], ['grey', 'Grey']];
  const LIGHT_Q = /prefers-color-scheme:\s*light/;
  const DARK_Q = /prefers-color-scheme:\s*dark/;
  const os = window.matchMedia('(prefers-color-scheme: light)');

  function saved() {
    try { return localStorage.getItem(KEY) || 'system'; } catch (e) { return 'system'; }
  }

  function rules(list, out) {
    for (const r of list) {
      if (r.media && r.cssRules) {
        if (r.__rxMedia === undefined) r.__rxMedia = r.media.mediaText;
        if (LIGHT_Q.test(r.__rxMedia)) out.push(r);
        rules(r.cssRules, out);
      }
    }
    return out;
  }

  function isLight(theme) {
    return theme === 'light' || theme === 'grey' || (theme === 'system' && os.matches);
  }

  function apply(theme) {
    if (!THEMES.some(t => t[0] === theme)) theme = 'system';
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.scheme = isLight(theme) ? 'light' : 'dark';
    for (const sheet of document.styleSheets) {
      let list;
      try { list = sheet.cssRules; } catch (e) { continue; }
      rules(list, []).forEach(r => {
        r.media.mediaText = theme === 'system' ? r.__rxMedia : (isLight(theme) ? 'all' : 'not all');
      });
    }
    // the logo has a light and a dark file
    document.querySelectorAll('picture source[media]').forEach(s => {
      if (s.dataset.rxMedia === undefined) s.dataset.rxMedia = s.media;
      if (DARK_Q.test(s.dataset.rxMedia)) s.media = theme === 'system' ? s.dataset.rxMedia : (isLight(theme) ? 'not all' : 'all');
    });
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.content = isLight(theme) ? 'light' : 'dark';
    const sel = document.getElementById('theme');
    if (sel) sel.value = theme;
    window.dispatchEvent(new CustomEvent('kk-theme', { detail: { theme, light: isLight(theme) } }));
  }

  function set(theme) {
    try { localStorage.setItem(KEY, theme); } catch (e) { /* not remembered */ }
    apply(theme);
  }

  function picker() {
    const nav = document.querySelector('header.top nav.controls');
    if (!nav || document.getElementById('theme')) return;
    const sel = document.createElement('select');
    sel.id = 'theme';
    sel.className = 'theme-pick';
    sel.setAttribute('aria-label', 'Theme');
    sel.title = 'Theme (this browser)';
    THEMES.forEach(([v, t]) => {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = t;
      sel.appendChild(o);
    });
    sel.addEventListener('change', () => set(sel.value));
    nav.appendChild(sel);
  }

  window.KKTheme = { apply, set, get: saved, isLight: () => isLight(saved()) };
  os.addEventListener('change', () => { if (saved() === 'system') apply('system'); });

  function init() {
    picker();
    apply(saved());
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
