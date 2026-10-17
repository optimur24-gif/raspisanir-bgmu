(() => {
  const key = 'bgmu:theme';
  const system = matchMedia('(prefers-color-scheme: dark)');
  const read = () => { try { const value = JSON.parse(localStorage.getItem(key)); return ['light','dark'].includes(value) ? value : null; } catch { return null; } };
  let choice = read();
  let active;
  const icons = {
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.4 1.4m11.2 11.2L19 19M5 19l1.4-1.4M17.6 6.4 19 5"/>',
    dark: '<path d="M20.5 13A8.7 8.7 0 0 1 11 3.5a8.7 8.7 0 1 0 9.5 9.5Z"/>',
  };
  function apply() {
    active = choice || (system.matches ? 'dark' : 'light');
    document.documentElement.dataset.theme = active;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', active === 'dark' ? '#0e1010' : '#ffffff');
    const button = document.getElementById('theme-toggle');
    if (!button) return;
    const next = active === 'dark' ? 'light' : 'dark';
    button.setAttribute('aria-label', next === 'dark' ? 'Включить тёмную тему' : 'Включить светлую тему');
    button.title = button.getAttribute('aria-label');
    button.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[next]}</svg>`;
  }
  apply();
  document.addEventListener('DOMContentLoaded', () => {
    apply();
    document.getElementById('theme-toggle').addEventListener('click', () => {
      choice = active === 'dark' ? 'light' : 'dark';
      try { localStorage.setItem(key, JSON.stringify(choice)); } catch { /* Theme still works for this visit. */ }
      apply();
    });
  });
  system.addEventListener('change', () => { if (!choice) apply(); });
  addEventListener('storage', event => { if (event.key === key || event.key === null) { choice = read(); apply(); } });
})();
