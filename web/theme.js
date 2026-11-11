'use strict';
// Runs before the stylesheet, independently of the query app and its API requests.
(() => {
  const key = 'anna.theme';
  const root = document.documentElement;
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const nextPreference = {light: 'dark', dark: 'system', system: 'light'};
  const normalize = value => ['light', 'dark'].includes(value) ? value : 'system';
  const read = () => normalize(annaKit.stored.get(key));
  const labels = {
    zh: {system: '跟随系统', light: '浅色', dark: '深色', switchTo: '切换为{next}（当前：{current}）'},
    en: {system: 'System', light: 'Light', dark: 'Dark', switchTo: 'Switch to {next} (current: {current})'}
  };
  const ui = labels[root.lang.startsWith('en') ? 'en' : 'zh'];
  let preference = read();
  let control;
  function apply() {
    root.dataset.theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    if (control) {
      const next = nextPreference[preference];
      const label = ui.switchTo.replace('{next}', ui[next]).replace('{current}', ui[preference]);
      control.dataset.preference = preference;
      control.setAttribute('aria-label', label);
      control.title = label;
    }
  }
  function choose(value) {
    preference = normalize(value);
    annaKit.stored.set(key, preference === 'system' ? null : preference); // Refused storage still switches this page.
    apply();
  }
  apply();
  system.addEventListener('change', apply);
  annaKit.stored.watch(key, () => { preference = read(); apply(); });
  document.addEventListener('DOMContentLoaded', () => {
    control = document.querySelector('#theme');
    if (!control) return; // Source-text pages share the theme, without the query toolbar.
    apply();
    control.hidden = false;
    control.addEventListener('click', () => choose(nextPreference[preference]));
  });
})();
