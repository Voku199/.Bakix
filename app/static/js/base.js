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
  console.log('[bakix:push] SW register start', {
    standalone: !!navigator.standalone || window.matchMedia('(display-mode: standalone)').matches,
    controller: !!navigator.serviceWorker.controller,
  });

  navigator.serviceWorker.register('/sw.js').then(function (reg) {
    console.log('[bakix:push] SW register success', {
      scope: reg.scope,
      active: reg.active ? reg.active.state : 'none',
      installing: reg.installing ? reg.installing.state : 'none',
      waiting: reg.waiting ? reg.waiting.state : 'none',
    });
    return navigator.serviceWorker.ready;
  }).then(function (readyReg) {
    console.log('[bakix:push] SW ready after register', {
      scope: readyReg.scope,
      active: readyReg.active ? readyReg.active.state : 'none',
    });
  }).catch(function (err) {
    console.error('[bakix:push] SW register failed', err);
  });
} else {
  console.warn('[bakix:push] SW register skipped', {
    secureContext: !!window.isSecureContext,
    origin: location.origin,
    reason: 'navigator.serviceWorker unavailable',
  });
}

