/* CSRF — auto-attach the token to every same-origin mutating fetch, so each
   caller (chat, dashboard, push, login…) doesn't have to remember to. The
   server (Flask-WTF) checks the X-CSRFToken header. */
(function () {
  var meta  = document.querySelector('meta[name="csrf-token"]');
  var token = meta ? meta.getAttribute('content') : '';
  if (!token || !window.fetch) return;
  var SAFE   = /^(GET|HEAD|OPTIONS|TRACE)$/i;
  var _fetch = window.fetch;
  window.fetch = function (input, init) {
    init = init || {};
    var method = (init.method ||
                  (input && typeof input === 'object' && input.method) ||
                  'GET').toUpperCase();
    var url    = typeof input === 'string' ? input : (input && input.url) || '';
    var sameOrigin = url.indexOf('http') !== 0 || url.indexOf(location.origin) === 0;
    if (!SAFE.test(method) && sameOrigin) {
      var headers = new Headers(init.headers ||
                    (input && typeof input === 'object' ? input.headers : null) || {});
      if (!headers.has('X-CSRFToken')) headers.set('X-CSRFToken', token);
      init.headers = headers;
    }
    return _fetch(input, init);
  };
})();

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

