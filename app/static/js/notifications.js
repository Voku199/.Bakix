/* notifications.js — iOS standalone-safe Web Push flow with explicit UI toggles */
(function () {
  'use strict';

  function _d(msg, data) {
    if (data !== undefined) console.log('[bakix:push] ' + msg, data);
    else console.log('[bakix:push] ' + msg);
  }

  var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent) && !window.MSStream;
  var isStandalone = !!navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;

  var notifWrap = document.getElementById('notif-wrap');
  var enableBtn = document.getElementById('notif-enable-btn');
  var testBtn = document.getElementById('notif-test-btn');
  var promptRow = document.getElementById('push-prompt-row');
  var promptBtn = document.getElementById('push-prompt-btn');
  var promptDismiss = document.getElementById('push-prompt-dismiss');
  var iosPromptRow = document.getElementById('ios-prompt-row');
  var iosPromptDismiss = document.getElementById('ios-prompt-dismiss');
  var notifStatus = document.getElementById('notif-status');

  var isSecureContextForPush = !!window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1';

  var hasSupport = ('serviceWorker' in navigator) && ('PushManager' in window) && ('Notification' in window);
  var vapidPublicKey = (window.VAPID_PUBLIC_KEY || '').trim();
  var _swReg = null;
  var _subscribing = false;
  var _dismissed = sessionStorage.getItem('push-prompt-dismissed') === '1';
  var _lastPermission = ('Notification' in window) ? Notification.permission : 'unsupported';

  _d('init', {
    isIOS: isIOS,
    standalone: isStandalone,
    secureContext: isSecureContextForPush,
    origin: location.origin,
    support: hasSupport,
    permission: _lastPermission,
    vapidKeyPresent: !!vapidPublicKey,
  });

  if (!enableBtn || !testBtn) {
    _d('notification buttons not found, aborting');
    return;
  }

  function setStatus(text) {
    if (!notifStatus) return;
    notifStatus.textContent = text || '';
  }

  function showUnavailable(buttonText, statusText) {
    if (notifWrap) notifWrap.style.display = '';
    enableBtn.style.display = 'block';
    testBtn.style.display = 'none';
    enableBtn.disabled = true;
    enableBtn.textContent = buttonText;
    enableBtn.title = statusText || buttonText;
    if (promptRow) promptRow.style.display = 'none';
    setStatus(statusText || buttonText);
    _d('notifications unavailable', {
      buttonText: buttonText,
      statusText: statusText,
      secureContext: isSecureContextForPush,
      standalone: isStandalone,
      support: hasSupport,
    });
  }

  function trackPermissionTransition(source) {
    if (!('Notification' in window)) return;
    var current = Notification.permission;
    if (current !== _lastPermission) {
      _d('permission transition [' + source + ']: ' + _lastPermission + ' -> ' + current);
      _lastPermission = current;
    } else {
      _d('permission check [' + source + ']: ' + current);
    }
  }

  function updatePermissionUI(source) {
    if (!('Notification' in window)) return;

    trackPermissionTransition(source);
    var permission = Notification.permission;
    var granted = permission === 'granted';
    var denied = permission === 'denied';

    enableBtn.style.display = granted ? 'none' : 'block';
    testBtn.style.display = granted ? 'block' : 'none';
    enableBtn.disabled = _subscribing || denied;
    testBtn.disabled = _subscribing || !_swReg;

    if (denied) {
      enableBtn.textContent = 'Notifikace blokovany';
      enableBtn.title = 'Povol notifikace v nastaveni prohlizece';
      setStatus('Notifikace jsou blokovane v prohlizeci.');
    } else {
      enableBtn.textContent = _subscribing ? 'Nacitam…' : 'Povolit notifikace';
      enableBtn.title = 'Zapnout push notifikace';
      setStatus(granted ? 'Notifikace jsou povolene.' : 'Povol notifikace jednim klepnutim.');
    }

    testBtn.title = 'Poslat testovaci push notifikaci';
    if (promptRow) {
      promptRow.style.display = (permission === 'default' && !_dismissed) ? '' : 'none';
    }

    _d('ui updated [' + source + ']', {
      permission: permission,
      enableVisible: enableBtn.style.display,
      testVisible: testBtn.style.display,
      subscribing: _subscribing,
    });
  }

  function urlBase64ToUint8Array(b64) {
    var pad = '='.repeat((4 - (b64.length % 4)) % 4);
    var raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function waitForServiceWorkerReady() {
    _d('awaiting serviceWorker.ready');
    return navigator.serviceWorker.ready.then(function (swReg) {
      _swReg = swReg;
      _d('serviceWorker.ready resolved', {
        scope: swReg.scope,
        activeState: swReg.active ? swReg.active.state : 'none',
      });
      return swReg;
    });
  }

  function syncSubscription(sub) {
    return fetch('/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(sub.toJSON()),
    }).then(function (r) {
      _d('/api/push/subscribe status', r.status);
      if (!r.ok) {
        return r.text().then(function (txt) {
          throw new Error('HTTP ' + r.status + ': ' + txt.substring(0, 120));
        });
      }
      return r.json();
    }).then(function (body) {
      _d('/api/push/subscribe body', body);
      if (body && body.ok === false) {
        throw new Error(body.error || 'subscribe rejected by server');
      }
    });
  }

  function ensureSubscription() {
    if (!('Notification' in window) || Notification.permission !== 'granted') {
      _d('ensureSubscription skipped: permission not granted');
      return Promise.resolve();
    }
    if (!_swReg) {
      _d('ensureSubscription skipped: _swReg missing');
      return Promise.resolve();
    }
    if (!vapidPublicKey) {
      _d('ensureSubscription skipped: missing VAPID key');
      return Promise.resolve();
    }

    _subscribing = true;
    updatePermissionUI('ensureSubscription:start');

    return _swReg.pushManager.getSubscription().then(function (existingSub) {
      if (existingSub) {
        _d('existing subscription found, syncing');
        return syncSubscription(existingSub);
      }

      _d('creating new push subscription');
      return _swReg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
      }).then(function (sub) {
        _d('pushManager.subscribe resolved', {
          endpoint: sub.endpoint.substring(0, 60) + '...',
        });
        return syncSubscription(sub);
      });
    }).catch(function (err) {
      _d('ensureSubscription failed', String(err));
      console.error('[bakix:push] ensureSubscription error:', err);
    }).finally(function () {
      _subscribing = false;
      updatePermissionUI('ensureSubscription:done');
    });
  }

  function requestPermissionFromGesture(source) {
    if (!('Notification' in window)) return;

    _d('requestPermission start [' + source + ']', {
      permissionBefore: Notification.permission,
      standalone: isStandalone,
    });

    Notification.requestPermission().then(function (permission) {
      _d('requestPermission resolved [' + source + ']', {
        permission: permission,
        now: Notification.permission,
      });

      // Immediate UI switch after grant (required for iOS standalone UX).
      updatePermissionUI('permission:' + source);

      if (permission === 'granted') {
        waitForServiceWorkerReady().then(function () {
          return ensureSubscription();
        });
      }
    }).catch(function (err) {
      _d('requestPermission rejected [' + source + ']', String(err));
      updatePermissionUI('permission-error:' + source);
    });
  }

  function sendTestNotification() {
    _d('send test notification requested');
    testBtn.disabled = true;

    fetch('/api/push/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'Bakix', body: 'Testovaci notifikace funguje!' }),
    }).then(function (r) {
      _d('/api/push/send status', r.status);
      return r.json();
    }).then(function (data) {
      _d('/api/push/send body', data);
      testBtn.textContent = data.ok ? 'Notifikace odeslana' : 'Chyba: ' + (data.error || '?');
    }).catch(function (err) {
      _d('/api/push/send failed', String(err));
      testBtn.textContent = 'Sitova chyba';
    }).finally(function () {
      setTimeout(function () {
        testBtn.textContent = 'Poslat testovaci notifikaci';
        testBtn.disabled = false;
      }, 2200);
    });
  }

  if (isIOS && !isStandalone) {
    _d('iOS without standalone mode: skip push flow');
    showUnavailable(
      'Na iOS jen v nainstalovane aplikaci',
      'V Safari dej Sdilet -> Pridat na plochu. Pak otevri PWA z plochy.'
    );
    if (promptRow) promptRow.style.display = 'none';
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
    return;
  }

  if (!isSecureContextForPush) {
    showUnavailable(
      'Notifikace vyzaduji HTTPS',
      'Na mobilu otevri aplikaci pres HTTPS. HTTP adresa push nepodporuje.'
    );
    return;
  }

  if (!hasSupport) {
    showUnavailable(
      'Push API neni dostupne',
      'Tento prohlizec nebo rezim nepodporuje service worker/push notifikace.'
    );
    return;
  }

  if (notifWrap) notifWrap.style.display = '';

  enableBtn.addEventListener('click', function () {
    _d('enable button click');
    requestPermissionFromGesture('enable-btn');
  });

  testBtn.addEventListener('click', function () {
    _d('test button click');
    sendTestNotification();
  });

  if (promptBtn) {
    promptBtn.addEventListener('click', function () {
      _d('prompt button click');
      requestPermissionFromGesture('prompt-btn');
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

  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) updatePermissionUI('visibilitychange');
  });

  updatePermissionUI('page-load');

  waitForServiceWorkerReady().then(function () {
    return ensureSubscription();
  }).catch(function (err) {
    _d('serviceWorker.ready rejected', String(err));
    console.error('[bakix:push] serviceWorker.ready error:', err);
  });
})();
