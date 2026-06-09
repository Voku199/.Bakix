/* push.js — Web Push permission state machine
 *
 * Platform matrix:
 *   iOS < 16.4                : Push unsupported. Show install hint (no-op for push).
 *   iOS 16.4+ in Safari       : Push unsupported. Must install PWA first → show hint.
 *   iOS 16.4+ standalone PWA  : Push supported. Normal subscribe flow.
 *   Android / Chrome          : Push supported. Normal subscribe flow.
 *   Desktop browsers          : Push supported (where Notification API exists).
 *
 * iOS 16.4+ hard rules enforced here:
 *   1. requestPermission() called synchronously within the user-gesture stack —
 *      no .then() / await before it in the click handler path.
 *   2. Event listeners attached BEFORE navigator.serviceWorker.ready resolves
 *      so a user tap is never lost during the SW activation window.
 *   3. userVisibleOnly: true is mandatory.
 *
 * Exposed API:
 *   window.requestPushPermission()  — call from any button's click handler.
 *                                     Do NOT call on page load or from setTimeout.
 *
 * Remote debug on iOS:
 *   Safari → Develop → [device] → [page] → Console (filter: [bakix:push])
 */
(function () {
  'use strict';

  // ── Diagnostic logger ────────────────────────────────────────────────────
  function _d(msg, data) {
    if (data !== undefined) console.log('[bakix:push] ' + msg, data);
    else                    console.log('[bakix:push] ' + msg);
  }

  // ── Platform detection ───────────────────────────────────────────────────
  // navigator.standalone  → true when running as iOS home-screen PWA
  // display-mode:standalone → true on Android/Chrome installed PWA
  var isIOS        = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  var isStandalone = !!navigator.standalone
                     || window.matchMedia('(display-mode: standalone)').matches;

  _d('init', {
    isIOS:        isIOS,
    isStandalone: isStandalone,
    SW:           'serviceWorker' in navigator,
    PushManager:  'PushManager'   in window,
    Notification: 'Notification'  in window,
    permission:   ('Notification' in window) ? Notification.permission : 'N/A',
    UA:           navigator.userAgent.substring(0, 80),
  });

  // ── DOM refs ─────────────────────────────────────────────────────────────
  var notifWrap        = document.getElementById('notif-wrap');
  var btn              = document.getElementById('notif-btn');
  var headerBtn        = document.getElementById('header-notif-btn');
  var promptRow        = document.getElementById('push-prompt-row');
  var promptBtn        = document.getElementById('push-prompt-btn');
  var promptDismiss    = document.getElementById('push-prompt-dismiss');
  var iosPromptRow     = document.getElementById('ios-prompt-row');
  var iosPromptDismiss = document.getElementById('ios-prompt-dismiss');

  if (!btn) { _d('notif-btn not found — aborting'); return; }

  // ── iOS in Safari (not installed) ────────────────────────────────────────
  // Push API is unavailable outside standalone mode on iOS.
  // Show an "Add to Home Screen" guide and bail out of the push flow.

  if (isIOS && !isStandalone) {
    _d('iOS non-standalone: push unavailable; showing install hint');
    if (iosPromptRow && sessionStorage.getItem('ios-prompt-dismissed') !== '1') {
      iosPromptRow.style.display = '';
    }
    if (iosPromptDismiss) {
      iosPromptDismiss.addEventListener('click', function () {
        sessionStorage.setItem('ios-prompt-dismissed', '1');
        if (iosPromptRow) iosPromptRow.style.display = 'none';
        _d('iOS install hint dismissed');
      });
    }
    return; // push flow not available here
  }

  // ── Feature detection (non-iOS or iOS standalone) ────────────────────────
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    _d('Push API not available on this platform — hiding UI');
    return; // notif-wrap stays display:none
  }

  // APIs confirmed: reveal settings notification section + header bell
  if (notifWrap) notifWrap.style.display = '';
  if (headerBtn) headerBtn.style.display = '';

  var VAPID_PUBLIC_KEY = (window.VAPID_PUBLIC_KEY || '').trim();
  if (!VAPID_PUBLIC_KEY) {
    console.warn('[bakix:push] VAPID_PUBLIC_KEY is empty — subscribe will fail');
  }

  // ── Module state — read synchronously on click, updated by setUI() ───────
  var _swReg     = null;   // set once SW is ready
  var _state     = 'loading';
  var _dismissed = sessionStorage.getItem('push-prompt-dismissed') === '1';

  // ── Helpers ──────────────────────────────────────────────────────────────

  function urlBase64ToUint8Array(b64) {
    var pad = '='.repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  // ── UI state machine ─────────────────────────────────────────────────────
  // Every state change goes through setUI() so _state and the DOM stay in sync.

  function setUI(state) {
    _state       = state;
    btn.disabled = false;
    _d('state', state + '  (permission=' + Notification.permission + ')');

    switch (state) {
      case 'loading':
        btn.textContent = 'Nacitam…';
        btn.disabled    = true;
        btn.title       = '';
        break;
      case 'denied':
        btn.textContent = 'Notifikace blokovany';
        btn.disabled    = true;
        btn.title       = 'Povol notifikace v nastaveni prohlizece';
        if (headerBtn) { headerBtn.style.display = 'none'; }
        break;
      case 'subscribed':
        btn.textContent = 'Poslat testovaci notifikaci';
        btn.title       = 'Odesle testovaci push notifikaci';
        if (headerBtn) { headerBtn.title = 'Testovat push notifikaci'; headerBtn.style.opacity = '1'; }
        break;
      default: // 'default'
        btn.textContent = 'Povolit notifikace';
        btn.title       = 'Zapnout push notifikace';
        if (headerBtn) { headerBtn.title = 'Povolit push notifikace'; headerBtn.style.opacity = '1'; }
    }

    // Dashboard invite row: visible only when push is available but not yet requested
    if (promptRow) {
      promptRow.style.display = (state === 'default' && !_dismissed) ? '' : 'none';
    }
  }

  function readState(swReg) {
    _d('readState  permission=' + Notification.permission);
    if (Notification.permission === 'denied') return Promise.resolve('denied');
    return swReg.pushManager.getSubscription().then(function (sub) {
      _d('existing sub endpoint', sub ? sub.endpoint.substring(0, 55) + '...' : null);
      return sub ? 'subscribed' : 'default';
    });
  }

  // ── Subscribe flow ───────────────────────────────────────────────────────
  // Designed to be called synchronously from a click event handler.
  // requestPermission() is the FIRST async call — still within the
  // user-gesture stack on iOS. Nothing async precedes it.

  function doSubscribe() {
    _d('doSubscribe: requesting permission');
    setUI('loading');

    Notification.requestPermission().then(function (perm) {
      _d('requestPermission result', perm);
      if (perm !== 'granted') {
        setUI(Notification.permission === 'denied' ? 'denied' : 'default');
        return;
      }

      _d('pushManager.subscribe  keyLen=' + VAPID_PUBLIC_KEY.length);
      _swReg.pushManager.subscribe({
        userVisibleOnly:      true,      // mandatory on iOS
        applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY),
      }).then(function (sub) {
        var subJson = sub.toJSON();
        _d('pushManager.subscribe OK', {
          endpoint:  sub.endpoint.substring(0, 55) + '...',
          hasP256dh: !!(subJson.keys && subJson.keys.p256dh),
          hasAuth:   !!(subJson.keys && subJson.keys.auth),
        });

        return fetch('/api/push/subscribe', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify(subJson),
        }).then(function (r) {
          _d('/subscribe HTTP', r.status);
          if (!r.ok) {
            return r.text().then(function (t) {
              throw new Error('HTTP ' + r.status + ': ' + t.substring(0, 80));
            });
          }
          return r.json();
        }).then(function (d) {
          _d('/subscribe body', d);
          if (d && !d.ok) throw new Error(d.error || 'server rejected subscription');
        });

      }).then(function () {
        setUI('subscribed');
      }).catch(function (err) {
        _d('subscribe error', String(err));
        console.error('[bakix:push] subscribe failed:', err);
        setUI('default');
      });

    }).catch(function (err) {
      _d('requestPermission threw', String(err));
      setUI('default');
    });
  }

  // ── Send test notification ────────────────────────────────────────────────

  function doSendTest() {
    _d('doSendTest');
    setUI('loading');
    fetch('/api/push/send', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ title: 'Bakix', body: 'Testovaci notifikace funguje!' }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        _d('/send response', d);
        btn.disabled    = false;
        btn.textContent = d.ok ? 'Notifikace odeslana ✓' : 'Chyba: ' + (d.error || '?');
        setTimeout(function () { setUI('subscribed'); }, 3000);
      })
      .catch(function (err) {
        _d('/send failed', String(err));
        btn.disabled    = false;
        btn.textContent = 'Sitova chyba';
        setTimeout(function () { setUI('subscribed'); }, 3000);
      });
  }

  // ── Public API ───────────────────────────────────────────────────────────
  // Call from a synchronous click event handler ONLY.
  // Calling from page load, setTimeout, or Promise.then breaks iOS.

  window.requestPushPermission = function () {
    _d('requestPushPermission()  state=' + _state + ' swReady=' + !!_swReg);
    if (!_swReg || _state === 'loading') { _d('SW not ready yet'); return; }
    if (_state === 'denied')             return;
    if (_state === 'subscribed')         { doSendTest(); return; }
    doSubscribe();
  };

  // ── Event listeners — attached IMMEDIATELY ───────────────────────────────
  // Attaching inside .ready.then() means iOS can miss a tap if the SW is
  // still activating. Attach now; guard with _swReg / _state instead.

  btn.addEventListener('click', function () {
    _d('btn click  state=' + _state + ' swReady=' + !!_swReg);
    if (!_swReg || _state === 'loading') return;
    if (_state === 'denied')             return;
    if (_state === 'subscribed')         { doSendTest(); return; }
    doSubscribe();
  });

  if (headerBtn) {
    headerBtn.addEventListener('click', function () {
      _d('headerBtn click  state=' + _state + ' swReady=' + !!_swReg);
      if (!_swReg || _state === 'loading') return;
      if (_state === 'denied')             return;
      if (_state === 'subscribed')         { doSendTest(); return; }
      doSubscribe();
    });
  }

  if (promptBtn) {
    promptBtn.addEventListener('click', function () {
      _d('prompt-btn click  state=' + _state);
      if (!_swReg || _state !== 'default') return;
      doSubscribe();
    });
  }

  if (promptDismiss) {
    promptDismiss.addEventListener('click', function () {
      _dismissed = true;
      sessionStorage.setItem('push-prompt-dismissed', '1');
      if (promptRow) promptRow.style.display = 'none';
      _d('push prompt dismissed');
    });
  }

  // ── Service Worker ready ─────────────────────────────────────────────────

  navigator.serviceWorker.ready.then(function (swReg) {
    _swReg = swReg;
    _d('SW ready', {
      scope:  swReg.scope,
      active: swReg.active ? swReg.active.state : 'none',
    });

    // Re-sync any live subscription to the server on every page load.
    // Covers silent browser-side token renewal (browser may update the
    // subscription object without notifying the page).
    swReg.pushManager.getSubscription().then(function (sub) {
      if (!sub) { _d('no active subscription on page load'); return; }
      _d('re-syncing existing subscription');
      fetch('/api/push/subscribe', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(sub.toJSON()),
      }).then(function (r) {
        _d('re-sync HTTP', r.status);
      }).catch(function (err) {
        _d('re-sync failed', String(err));
      });
    });

    readState(swReg).then(setUI);
  }).catch(function (err) {
    _d('serviceWorker.ready failed', String(err));
    console.error('[bakix:push] SW ready error:', err);
    if (notifWrap) notifWrap.style.display = 'none';
  });

})();

// ── Notification click navigation ────────────────────────────────────────────
// SW sends { type:'push-navigate', url } when the user taps a notification
// while the app is already open. Scroll to the target section if we're on the
// dashboard; otherwise navigate to the URL.
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.addEventListener('message', function (e) {
    if (!e.data || e.data.type !== 'push-navigate') return;
    var url   = e.data.url || '/';
    var parts = url.split('#');
    var hash  = parts[1] || '';
    if (hash && window.location.pathname === '/') {
      var el = document.getElementById(hash);
      if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
    }
    window.location.href = url;
  });
}
