/* i18n.js — language switcher with full-page loading overlay */
(function () {
  'use strict';

  function switchLanguage(lang) {
    var existing = document.getElementById('lang-switch-overlay');
    if (existing) return;

    var overlay = document.createElement('div');
    overlay.id = 'lang-switch-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:var(--bg,#f0ebe3);z-index:10000;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:.75rem;opacity:0;transition:opacity .18s ease;pointer-events:all';

    var label = (window._t && window._t.lang_switching) || 'Switching language…';

    overlay.innerHTML =
      '<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="animation:_i18n_spin 1s linear infinite;opacity:.5"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>' +
      '<span style="font-family:\'Space Mono\',monospace;font-size:12px;letter-spacing:.5px;color:var(--muted,#6b6560)">' + label + '</span>';

    if (!document.getElementById('_i18n_spin_kf')) {
      var st = document.createElement('style');
      st.id = '_i18n_spin_kf';
      st.textContent = '@keyframes _i18n_spin{to{transform:rotate(360deg)}}';
      document.head.appendChild(st);
    }

    document.body.appendChild(overlay);
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { overlay.style.opacity = '1'; });
    });
    setTimeout(function () {
      window.location.href = '/set-language/' + encodeURIComponent(lang);
    }, 160);
  }

  window.switchLanguage = switchLanguage;
})();
