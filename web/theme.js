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
    zh: {system: '跟随系统', light: '浅色', dark: '深色', switchTo: '切换为{next}（当前：{current}）', reset: 'Shift+点击 / Shift+Enter / Shift+空格：跟随系统'},
    en: {system: 'System', light: 'Light', dark: 'Dark', switchTo: 'Switch to {next} (current: {current})', reset: 'Shift+click / Shift+Enter / Shift+Space: follow system'}
  };
  const ui = labels[root.lang.startsWith('en') ? 'en' : 'zh'];
  let preference = read();
  let control;
  function apply() {
    root.dataset.theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    if (control) {
      const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
      const label = ui.switchTo.replace('{next}', ui[next]).replace('{current}', ui[preference]);
      control.dataset.preference = preference;
      control.setAttribute('aria-label', label);
      control.title = label + '\n' + ui.reset;
    }
  }
  function choose(value) {
    preference = normalize(value);
    try {
      if (preference === 'system') localStorage.removeItem(key);
      else localStorage.setItem(key, preference);
    } catch { /* Storage can be disabled; switching still works for this page. */ }
    apply();
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
    apply();
    control.hidden = false;
    control.addEventListener('click', event => {
      choose(event.shiftKey ? 'system' : root.dataset.theme === 'dark' ? 'light' : 'dark');
    });
    control.addEventListener('keydown', event => {
      if (!event.shiftKey || !['Enter', ' '].includes(event.key)) return;
      event.preventDefault();
      choose('system');
    });
  });
})();
