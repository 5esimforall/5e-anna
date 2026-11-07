'use strict';
// Runs before the stylesheet, independently of the query app and its API requests.
(() => {
  const key = 'anna.theme';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const normalize = value => ['light', 'dark'].includes(value) ? value : 'system';
  const read = () => {
    try { return normalize(localStorage.getItem(key)); }
    catch { return 'system'; }
  };
  const labels = {
    zh: {theme: '外观', system: '跟随系统', light: '浅色', dark: '深色'},
    en: {theme: 'Appearance', system: 'System', light: 'Light', dark: 'Dark'}
  };
  let preference = read();
  let control;
  function apply() {
    root.dataset.theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    if (control) control.value = preference;
  }
  apply();
  system.addEventListener('change', apply);
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = read();
    apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    control = document.querySelector('#theme');
    if (!control) return; // Source-text pages share the theme, without the query toolbar.
    const ui = labels[root.lang.startsWith('en') ? 'en' : 'zh'];
    document.querySelector('#theme-label').textContent = ui.theme;
    control.setAttribute('aria-label', ui.theme);
    for (const option of control.options) option.textContent = ui[option.value];
    apply();
    control.closest('label').hidden = false;
    control.addEventListener('change', () => {
      preference = normalize(control.value);
      try {
        if (preference === 'system') localStorage.removeItem(key);
        else localStorage.setItem(key, preference);
      } catch { /* Storage can be disabled; switching still works for this page. */ }
      apply();
    });
  });
})();
