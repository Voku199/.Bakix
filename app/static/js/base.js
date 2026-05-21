/* Theme toggle + service worker — runs on every page */
(function () {
  const root = document.documentElement;
  const btn  = document.getElementById('theme-toggle');
  const icon = document.getElementById('theme-icon');
  const ICONS = { light: '○', dark: '●', auto: '◐' };

  function getStored() {
    return localStorage.getItem('bakix-theme') || 'auto';
  }

  function applyTheme(t) {
    root.setAttribute('data-theme', t);
    if (icon) icon.textContent = ICONS[t] || ICONS.auto;
  }

  function cycle() {
    const order = ['auto', 'light', 'dark'];
    const cur   = getStored();
    const next  = order[(order.indexOf(cur) + 1) % order.length];
    localStorage.setItem('bakix-theme', next);
    applyTheme(next);
  }

  applyTheme(getStored());
  if (btn) btn.addEventListener('click', cycle);
})();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js');
}

