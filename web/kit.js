/* Helpers every page shares: HTML escaping and browser storage that keeps working when the browser refuses it. */
const annaKit = (() => {
  const entities = {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'};
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => entities[c]);
  // Preferences and remembered places live in localStorage; a refusal leaves the current page working.
  const stored = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) {
      try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); return true; }
      catch { return false; }
    },
    // Other tabs of this site report their writes; a cleared store reports a null key.
    watch(key, listener) {
      if (typeof window === 'undefined') return;
      window.addEventListener('storage', event => { if (event.key === key || event.key === null) listener(); });
    }
  };
  return {esc, stored};
})();
if (typeof module !== 'undefined') module.exports = annaKit;
