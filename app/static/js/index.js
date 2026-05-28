/* Settings overlay — index page */
(function () {
  const overlay = document.getElementById('settings-overlay');
  const status  = document.getElementById('settings-status');
  const THEME_ICONS = { light: '○', dark: '●', auto: '◐' };

  function openSettings() {
    fetch('/api/settings')
      .then(r => r.json())
      .then(d => {
        document.getElementById('s-display-name').value       = d.display_name || '';
        document.getElementById('s-school-url').value         = d.school_url   || '';
        document.getElementById('s-username').value           = d.username     || '';
        document.getElementById('s-password').value           = '';
        document.getElementById('s-remember').checked         = !!d.remember_password;
        document.getElementById('s-theme').value              = d.theme    || localStorage.getItem('bakix-theme') || 'auto';
        document.getElementById('s-language').value           = d.language || 'cs';
        document.getElementById('s-notif-messages').checked   = !!d.notifications_messages;
        document.getElementById('s-notif-homeworks').checked  = !!d.notifications_homeworks;
        document.getElementById('s-notif-marks').checked      = !!d.notifications_marks;
        document.getElementById('s-notif-subs').checked       = !!d.notifications_subs;
        document.getElementById('s-notif-daily').checked      = !!d.notifications_daily;
        document.getElementById('s-notif-absences').checked   = !!d.notifications_absences;
        status.textContent = '';
        status.className   = 'settings-status';
        overlay.classList.add('open');
      })
      .catch(() => {
        status.textContent = 'Nepodařilo se načíst nastavení.';
        status.className   = 'settings-status settings-status--err';
        overlay.classList.add('open');
      });
  }

  function closeSettings() { overlay.classList.remove('open'); }

  function saveSettings() {
    const displayName = (document.getElementById('s-display-name')?.value || '').trim();
    const payload = {
      school_url:             document.getElementById('s-school-url').value.trim(),
      username:               document.getElementById('s-username').value.trim(),
      password:               document.getElementById('s-password').value,
      remember_password:      document.getElementById('s-remember').checked,
      theme:                  document.getElementById('s-theme').value,
      language:               document.getElementById('s-language').value,
      notifications_messages:  document.getElementById('s-notif-messages').checked,
      notifications_homeworks: document.getElementById('s-notif-homeworks').checked,
      notifications_marks:     document.getElementById('s-notif-marks').checked,
      notifications_subs:      document.getElementById('s-notif-subs').checked,
      notifications_daily:     document.getElementById('s-notif-daily').checked,
      notifications_absences:  document.getElementById('s-notif-absences').checked,
    };

    const t = payload.theme;
    localStorage.setItem('bakix-theme', t);
    document.documentElement.setAttribute('data-theme', t);
    const icon = document.getElementById('theme-icon');
    if (icon) icon.textContent = THEME_ICONS[t] || THEME_ICONS.auto;

    status.textContent = 'Ukládám…';
    status.className   = 'settings-status';

    const saves = [
      fetch('/api/settings', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(payload),
      }).then(r => r.json()),
    ];
    if (displayName) {
      saves.push(
        fetch('/api/user/settings', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ display_name: displayName }),
        }).then(r => r.json())
      );
    }

    Promise.all(saves)
      .then(([d]) => {
        if (d.ok) {
          status.textContent = 'Uloženo.';
          status.className   = 'settings-status settings-status--ok';
          if (displayName) {
            const g = document.getElementById('dash-greeting');
            if (g) g.innerHTML = 'Vítej zpět, <strong>' + displayName.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</strong>!';
          }
          setTimeout(closeSettings, 800);
        } else {
          status.textContent = d.error || 'Chyba při ukládání.';
          status.className   = 'settings-status settings-status--err';
        }
      })
      .catch(() => {
        status.textContent = 'Síťová chyba.';
        status.className   = 'settings-status settings-status--err';
      });
  }

  document.getElementById('settings-open')  ?.addEventListener('click', openSettings);
  document.getElementById('settings-close') ?.addEventListener('click', closeSettings);
  document.getElementById('settings-cancel')?.addEventListener('click', closeSettings);
  document.getElementById('settings-save')  ?.addEventListener('click', saveSettings);
  overlay.addEventListener('click', e => { if (e.target === overlay) closeSettings(); });

})();

/* Desktop PWA install prompt (Chrome/Edge) */
(function () {
  'use strict';

  var installBtn = document.getElementById('pwa-install-btn');
  var installStatus = document.getElementById('pwa-install-status');
  if (!installBtn || !installStatus) return;

  var deferredPrompt = null;
  var ua = navigator.userAgent || '';
  var isIOS = /iphone|ipad|ipod/i.test(ua);
  var isStandalone = !!navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;
  var isSecure = !!window.isSecureContext || location.hostname === 'localhost' || location.hostname === '127.0.0.1';

  function setStatus(msg) {
    installStatus.textContent = msg || '';
  }

  function setInstalledState() {
    installBtn.disabled = true;
    installBtn.textContent = 'Aplikace je nainstalovana';
    setStatus('Bakix je nainstalovany v tomto zarizeni.');
  }

  if (!isSecure) {
    installBtn.disabled = true;
    setStatus('Instalace funguje jen pres HTTPS (nebo localhost).');
    return;
  }

  if (isStandalone) {
    setInstalledState();
    return;
  }

  if (isIOS) {
    installBtn.disabled = true;
    setStatus('Na iOS pouzij Safari -> Sdilet -> Pridat na plochu.');
    return;
  }

  setStatus('Pokud se tlacitko neaktivuje, otevri Bakix v Chrome nebo Edge.');

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    installBtn.disabled = false;
    installBtn.textContent = 'Nainstalovat aplikaci';
    setStatus('Instalace je pripravena.');
  });

  installBtn.addEventListener('click', function () {
    if (!deferredPrompt) {
      setStatus('Instalace zatim neni dostupna. Zkus obnovit stranku.');
      return;
    }

    installBtn.disabled = true;
    deferredPrompt.prompt();
    deferredPrompt.userChoice
      .then(function (choice) {
        if (choice && choice.outcome === 'accepted') {
          setStatus('Instalace potvrzena.');
        } else {
          installBtn.disabled = false;
          setStatus('Instalace zrusena.');
        }
      })
      .catch(function () {
        installBtn.disabled = false;
        setStatus('Nepodarilo se spustit instalaci.');
      })
      .finally(function () {
        deferredPrompt = null;
      });
  });

  window.addEventListener('appinstalled', function () {
    setInstalledState();
  });
})();

/* ----- Themes popup ----- */
(function () {
  'use strict';

  var popup = null;
  var activeAbbrev = null;
  var cache = {};

  function getPopup() {
    if (popup) return popup;
    popup = document.createElement('div');
    popup.id = 'themes-popup';
    popup.innerHTML =
      '<div class="themes-popup__header">' +
        '<span class="themes-popup__label" id="themes-popup-label"></span>' +
        '<button class="themes-popup__close" id="themes-popup-close" aria-label="Zavřít">×</button>' +
      '</div>' +
      '<div class="themes-popup__body" id="themes-popup-body"></div>';
    document.body.appendChild(popup);
    document.getElementById('themes-popup-close').addEventListener('click', hide);
    document.addEventListener('click', function (e) {
      if (!popup || !popup.classList.contains('visible')) return;
      if (popup.contains(e.target) || e.target.classList.contains('mv--detail')) return;
      hide();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') hide();
    });
    return popup;
  }

  function position(triggerRect) {
    var p     = popup;
    var margin = 10;
    var vw    = window.innerWidth;
    var vh    = window.innerHeight;
    var popW  = p.offsetWidth  || 240;
    var popH  = p.offsetHeight || 120;

    var left = triggerRect.right + margin;
    var top  = triggerRect.top;

    if (left + popW > vw - margin) left = triggerRect.left - popW - margin;
    if (left < margin) left = margin;
    if (top + popH > vh - margin) top = vh - popH - margin;
    if (top < margin) top = margin;

    p.style.left = left + 'px';
    p.style.top  = top  + 'px';
  }

  function renderThemes(themes) {
    var body = document.getElementById('themes-popup-body');
    if (!body) return;
    if (!themes.length) {
      body.innerHTML = '<span class="themes-popup__empty">Žádná témata nenalezena.</span>';
      return;
    }
    var ul = document.createElement('ul');
    ul.className = 'themes-popup__list';
    themes.forEach(function (t) {
      var li   = document.createElement('li');
      var name = document.createElement('span');
      name.className   = 'themes-popup__theme-name';
      name.textContent = t.name || t;
      li.appendChild(name);
      if (t.date) {
        var date = document.createElement('span');
        date.className   = 'themes-popup__theme-date';
        date.textContent = t.date;
        li.appendChild(date);
      }
      ul.appendChild(li);
    });
    body.innerHTML = '';
    body.appendChild(ul);
  }

  function show(triggerEl, abbrev, subjectName) {
    var p = getPopup();
    activeAbbrev = abbrev;

    document.getElementById('themes-popup-label').textContent = subjectName || abbrev;
    document.getElementById('themes-popup-body').innerHTML =
      '<span class="themes-popup__loading">Načítám…</span>';

    p.style.display = 'block';
    // measure before animating
    requestAnimationFrame(function () {
      position(triggerEl.getBoundingClientRect());
      p.classList.add('visible');
    });

    if (cache[abbrev]) {
      renderThemes(cache[abbrev]);
      return;
    }

    fetch('/api/3/subjects/themes/' + encodeURIComponent(abbrev))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (activeAbbrev !== abbrev) return;
        cache[abbrev] = data.themes || [];
        renderThemes(cache[abbrev]);
        // re-clamp height after content loads
        requestAnimationFrame(function () { position(triggerEl.getBoundingClientRect()); });
      })
      .catch(function () {
        if (activeAbbrev !== abbrev) return;
        var body = document.getElementById('themes-popup-body');
        if (body) body.innerHTML = '<span class="themes-popup__error">Nepodařilo se načíst témata.</span>';
      });
  }

  function hide() {
    if (!popup) return;
    popup.classList.remove('visible');
    activeAbbrev = null;
    // hide after transition
    setTimeout(function () {
      if (popup && !popup.classList.contains('visible')) popup.style.display = 'none';
    }, 200);
  }

  window.showMarkThemes = show;
})();

/* ----- Grade average calculator ----- */
(function () {
  'use strict';

  var _marksData = null; // set by initCalc() after marks load

  window.initCalc = function (items) {
    _marksData = items;
    var card = document.getElementById('calc-card');
    var sel  = document.getElementById('calc-subject');
    if (!card || !sel) return;

    sel.innerHTML = '';
    var hasUsable = false;
    items.forEach(function (s, i) {
      var numericCount = (s.Marks || []).filter(function (m) {
        var v = parseFloat(m.MarkText);
        return !isNaN(v) && v >= 1 && v <= 5 && !m.IsPoints;
      }).length;
      if (!numericCount) return;
      hasUsable = true;
      var opt = document.createElement('option');
      opt.value = i;
      opt.textContent = (s.Subject && s.Subject.Name) || ('Předmět ' + (i + 1));
      sel.appendChild(opt);
    });

    if (!hasUsable) return;
    card.style.display = '';
  };

  function runCalc() {
    var result = document.getElementById('calc-result');
    var sel    = document.getElementById('calc-subject');
    var inp    = document.getElementById('calc-target');
    if (!result || !sel || !inp || !_marksData) return;

    var idx    = parseInt(sel.value, 10);
    var target = parseFloat(inp.value);

    if (isNaN(idx) || idx < 0 || idx >= _marksData.length) {
      result.innerHTML = '<span class="calc-note">Vyber předmět.</span>'; return;
    }
    if (isNaN(target) || target < 1 || target > 5) {
      result.innerHTML = '<span class="calc-note">Zadej cílový průměr (1–5), např. 1.49 pro jedničku na vysvědčení.</span>'; return;
    }

    var marks        = _marksData[idx].Marks || [];
    var subjectName  = (_marksData[idx].Subject && _marksData[idx].Subject.Name) || 'tohoto předmětu';
    var wSum = 0, wTotal = 0;
    (marks || []).forEach(function (m) {
      var v = parseFloat(m.MarkText);
      if (isNaN(v) || v < 1 || v > 5 || m.IsPoints) return;
      var w  = m.Weight || 1;
      wSum   += v * w;
      wTotal += w;
    });

    if (wTotal === 0) {
      result.innerHTML = '<span class="calc-note">Žádné číselné známky v tomto předmětu.</span>'; return;
    }

    var currentAvg = wSum / wTotal;
    var html = '<div class="calc-current">Aktuální průměr z ' + subjectName +
               ': <strong>' + currentAvg.toFixed(2) + '</strong></div>';

    if (target >= currentAvg - 0.005) {
      html += '<span class="calc-note">Průměr ' + currentAvg.toFixed(2) +
              ' je již na cíli nebo lepší — není co zlepšovat.</span>';
      result.innerHTML = html; return;
    }

    // target=1.00 is mathematically unreachable (infinite 1s needed)
    if (target <= 1.005) {
      html += '<p class="calc-note" style="margin:.5rem 0 0;">Průměr přesně 1,00 nelze nikdy dosáhnout — ' +
              'jedničky průměr jen nekonečně přibližují k 1,00, ale nikdy na 1,00 nedorazí.<br>' +
              '<strong>Tip:</strong> Zadej <strong>1,49</strong> — to obvykle stačí pro jedničku na vysvědčení.</p>';
      result.innerHTML = html; return;
    }

    // n = ceil((wSum - target * wTotal) / (w * (target - 1)))
    // = how many grade-1 tests (weight w) are needed to reach target
    html += '<div class="calc-subhead">Kolik jedniček potřebuješ pro průměr ≤ ' + target.toFixed(2) + ':</div>';
    html += '<div>';
    [1, 2, 3].forEach(function (w) {
      var rawN = (wSum - target * wTotal) / (w * (target - 1));
      var n    = Math.ceil(Math.max(1, rawN));

      html += '<div class="calc-row-result">';
      html += '<span class="calc-row-weight">Váha ' + w + '</span>';

      if (n > 30) {
        html += '<span class="calc-note">Více než 30 jedniček — příliš daleko od cíle</span>';
      } else {
        var achieved = (wSum + n * w) / (wTotal + n * w);
        var label    = n === 1 ? 'jednička' : n <= 4 ? 'jedničky' : 'jedniček';
        html += '<strong>' + n + ' ' + label + '</strong>';
        html += ' <span class="calc-achieved">→ průměr bude <strong>' + achieved.toFixed(2) + '</strong></span>';
      }

      html += '</div>';
    });
    html += '</div>';
    result.innerHTML = html;
  }

  document.addEventListener('DOMContentLoaded', function () {
    var btn     = document.getElementById('calc-btn');
    var inp     = document.getElementById('calc-target');
    var helpBtn = document.getElementById('calc-help-btn');
    var helpBox = document.getElementById('calc-help-box');

    if (btn)     btn.addEventListener('click', runCalc);
    if (inp)     inp.addEventListener('keydown', function (e) { if (e.key === 'Enter') runCalc(); });
    if (helpBtn) helpBtn.addEventListener('click', function () {
      var open = helpBox.style.display === 'none';
      helpBox.style.display = open ? '' : 'none';
      helpBtn.style.background = open ? 'var(--accent)' : '';
      helpBtn.style.color      = open ? '#fff' : '';
    });
  });
})();

/* ----- Dashboard data fetching (formerly index.js) ----- */
document.addEventListener('DOMContentLoaded', function () {
  'use strict';

  const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  function fmtDate(iso) { return iso ? iso.substring(0, 10) : ''; }

  function isWithin24h(dateStr) {
    if (!dateStr) return false;
    const end = new Date(dateStr).getTime();
    const now = Date.now();
    return end > now && (end - now) < 86400000;
  }

  function setCard(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
  }

  const _STATUS_LABELS = {
    Cancelled:     'Odpadlo',
    Substitution:  'Suplování',
    TeacherChange: 'Náhradník',
    RoomChange:    'Jiná učebna',
    Absent:        'Absence',
  };

  function renderTimetable(items) {
    if (!items.length) { setCard('events-body', '<p class="empty">Dnes máš volno, užij si den! ✦</p>'); return; }
    const frag = document.createDocumentFragment();
    items.forEach(function (h) {
      const isCancelled = h.status === 'Cancelled';
      const isChanged   = h.status && h.status !== 'OK';

      const row = document.createElement('div');
      row.className = 'tt-row' +
        (isCancelled ? ' tt-row--cancelled' : isChanged ? ' tt-row--changed' : '');

      const timeEl = document.createElement('div');
      timeEl.className = 'tt-row__time';
      if (h.time && h.time !== '—') {
        var parts = h.time.split('-');
        timeEl.textContent = parts[0] || h.time;
      } else {
        timeEl.textContent = '—';
      }

      const main = document.createElement('div');

      const subj = document.createElement('div');
      subj.className = 'tt-row__subject';
      subj.textContent = h.subject || '—';
      if (isChanged) {
        const badge = document.createElement('span');
        badge.className = 'tt-badge ' + (isCancelled ? 'tt-badge--cancelled' : 'tt-badge--changed');
        badge.textContent = _STATUS_LABELS[h.status] || h.status;
        subj.appendChild(badge);
      }

      const metaParts = [h.time, h.room, h.teacher].filter(function (x) { return x && x !== '—'; });
      const meta = document.createElement('div');
      meta.className = 'tt-row__meta';
      meta.textContent = metaParts.join(' · ');

      main.appendChild(subj);
      main.appendChild(meta);
      if (h.change_info) {
        const info = document.createElement('div');
        info.className = 'tt-row__meta';
        info.textContent = h.change_info;
        main.appendChild(info);
      }

      row.appendChild(timeEl);
      row.appendChild(main);
      frag.appendChild(row);
    });
    const body = document.getElementById('events-body');
    body.innerHTML = '';
    body.appendChild(frag);
  }

  function renderHomeworks(items) {
    if (!items.length) { setCard('homeworks-body', '<p class="empty">Žádné úkoly — skvělá práce! ✓</p>'); return; }
    const frag = document.createDocumentFragment();
    items.forEach(function (hw) {
      const div = document.createElement('div');
      div.className = 'hw-item' + (isWithin24h(hw.DateEnd) ? ' hw-item--urgent' : '');
      const title = document.createElement('div');
      title.className = 'hw-item__title';
      title.textContent = hw.Subject || '—';
      if (hw.HasAttachments) {
        const a = document.createElement('span'); a.className = 'hw-item__attach'; a.textContent = ' 📎';
        title.appendChild(a);
      }
      const meta = document.createElement('div');
      meta.className = 'hw-item__meta'; meta.textContent = fmtDate(hw.DateEnd);
      div.appendChild(title); div.appendChild(meta);
      if (hw.Content) {
        const c = document.createElement('div'); c.className = 'hw-item__text'; c.textContent = hw.Content;
        div.appendChild(c);
      }
      frag.appendChild(div);
    });
    const body = document.getElementById('homeworks-body');
    body.innerHTML = ''; body.appendChild(frag);
  }

  function renderKomens(items) {
    if (!items.length) { setCard('komens-body', '<p class="empty">Žádné nové zprávy. Klid a pohoda.</p>'); return; }
    const frag = document.createDocumentFragment();
    items.forEach(function (m) {
      const div = document.createElement('div');
      div.className = 'msg-item' + (!m.Read ? ' msg-item--unread' : '');
      const title = document.createElement('div'); title.className = 'msg-item__title'; title.textContent = m.Title || '—';
      const meta  = document.createElement('div'); meta.className  = 'msg-item__meta';
      meta.textContent = (m.Sender || '—') + ' · ' + fmtDate(m.SentDate);
      div.appendChild(title); div.appendChild(meta);
      if (m.Text) {
        const t = document.createElement('div'); t.className = 'msg-item__text';
        t.textContent = m.Text.length > 60 ? m.Text.slice(0, 60) + '…' : m.Text;
        div.appendChild(t);
      }
      div.addEventListener('click', function () { if (window.openMsgModal) window.openMsgModal(m); });
      frag.appendChild(div);
    });
    const body = document.getElementById('komens-body');
    body.innerHTML = ''; body.appendChild(frag);
  }

  function renderMarks(items) {
    if (!items.length) { setCard('marks-body', '<p class="empty">Žádné předměty.</p>'); return; }
    const frag = document.createDocumentFragment();
    items.forEach(function (s) {
      const row = document.createElement('div');
      row.className = 'marks-sum-row';

      const header = document.createElement('div');
      header.className = 'marks-sum-row__header';
      const name = document.createElement('span');
      const subjectName = (s.Subject && s.Subject.Name) || '—';
      name.textContent = subjectName;
      const abbrev = document.createElement('span');
      abbrev.className = 'subject__abbrev';
      const abbrevStr = ((s.Subject && s.Subject.Abbrev) || '').trim();
      abbrev.textContent = abbrevStr ? ' (' + abbrevStr + ')' : '';
      name.appendChild(abbrev);
      const arrow = document.createElement('span');
      arrow.className = 'marks-sum-row__arrow';
      arrow.textContent = '▾';
      name.appendChild(arrow);
      header.appendChild(name);
      const avg = (s.AverageText || '').trim();
      if (avg) {
        const b = document.createElement('span'); b.className = 'marks-sum-row__avg'; b.textContent = avg;
        header.appendChild(b);
      }
      row.appendChild(header);

      const detail = document.createElement('div');
      detail.className = 'marks-detail';
      (s.Marks || []).forEach(function (m) {
        const mrow = document.createElement('div');
        mrow.className = 'marks-detail-row';
        const mv = document.createElement('span');
        mv.className = 'mv mv--' + (parseInt(m.MarkText) || 'other') + (abbrevStr ? ' mv--detail' : '');
        mv.textContent = m.MarkText || '?';
        if (abbrevStr) {
          mv.title = 'Zobrazit témata';
          mv.addEventListener('click', function (e) {
            e.stopPropagation();
            if (window.showMarkThemes) window.showMarkThemes(mv, abbrevStr, subjectName);
          });
        }
        const cap = document.createElement('span');
        cap.className = 'marks-detail-caption';
        cap.textContent = m.Caption || '';
        const meta = document.createElement('span');
        meta.className = 'marks-detail-meta';
        meta.textContent = m.IsPoints ? 'body' : (m.Weight ? 'váha ' + m.Weight : '');
        mrow.appendChild(mv); mrow.appendChild(cap); mrow.appendChild(meta);
        detail.appendChild(mrow);
      });
      row.appendChild(detail);

      row.addEventListener('click', function () { row.classList.toggle('open'); });
      frag.appendChild(row);
    });
    const body = document.getElementById('marks-body');
    body.innerHTML = ''; body.appendChild(frag);
    if (window.initCalc) window.initCalc(items);
  }

  function cardErr(id, msg) { setCard(id, '<p class="card-error">' + esc(msg) + '</p>'); }

  const dashboard = document.getElementById('dashboard');
  if (!dashboard) return;

  fetch('/api/settings').then(function (r) { return r.json(); }).then(function (d) {
    var g = document.getElementById('dash-greeting');
    if (!g) return;
    var name = (d.display_name || d.username || '').trim();
    if (name) {
      g.innerHTML = 'Vítej zpět, <strong>' + esc(name) + '</strong>!';
    } else {
      g.style.display = 'none';
    }
  }).catch(function () {
    var g = document.getElementById('dash-greeting');
    if (g) g.style.display = 'none';
  });

  Promise.all([
    fetch('/api/dashboard/today').then(function (r) { return r.json(); }).catch(function () { return { error: 'Síťová chyba.' }; }),
    fetch('/api/3/homeworks').then(function (r) { return r.json(); }).catch(function () { return { error: 'Síťová chyba.' }; }),
    fetch('/api/3/komens/messages/received', { method: 'POST' }).then(function (r) { return r.json(); }).catch(function () { return { error: 'Síťová chyba.' }; }),
    fetch('/api/3/marks').then(function (r) { return r.json(); }).catch(function () { return { error: 'Síťová chyba.' }; }),
  ]).then(function (results) {
    const events    = results[0];
    const homeworks = results[1];
    const komens    = results[2];
    const marks     = results[3];

    if (events.error)    cardErr('events-body',    events.error);    else renderTimetable(events);
    if (homeworks.error) cardErr('homeworks-body', homeworks.error); else renderHomeworks(homeworks);
    if (komens.error)    cardErr('komens-body',    komens.error);    else renderKomens(komens);
    if (marks.error)     cardErr('marks-body',     marks.error);     else renderMarks(marks);
  });

  document.querySelectorAll('.tt-day-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.tt-day-btn').forEach(function (b) { b.classList.remove('tt-day-btn--active'); });
      this.classList.add('tt-day-btn--active');
      const url = this.dataset.day === 'tomorrow' ? '/api/dashboard/tomorrow' : '/api/dashboard/today';
      setCard('events-body', '<div class="skel-rows"><div class="skel-row"><div class="skel skel-sq"></div><div class="skel-col"><div class="skel skel-line"></div><div class="skel skel-line--short"></div></div></div><div class="skel-row"><div class="skel skel-sq"></div><div class="skel-col"><div class="skel skel-line"></div><div class="skel skel-line--xshort"></div></div></div></div>');
      fetch(url).then(function (r) { return r.json(); })
        .then(renderTimetable)
        .catch(function () { cardErr('events-body', 'Síťová chyba.'); });
    });
  });
});

/* ── Komens message modal ─────────────────────────────────── */
(function () {
  var modal      = document.getElementById('msg-modal');
  var modalTitle = document.getElementById('msg-modal-title');
  var modalMeta  = document.getElementById('msg-modal-meta');
  var modalBody  = document.getElementById('msg-modal-body');
  var closeBtn   = document.getElementById('msg-modal-close');
  if (!modal) return;

  function fmtDate(iso) { return iso ? iso.substring(0, 10) : ''; }

  window.openMsgModal = function (m) {
    modalTitle.textContent = m.Title  || '—';
    modalMeta.textContent  = (m.Sender || '—') + ' · ' + fmtDate(m.SentDate);
    modalBody.textContent  = m.Text   || '';
    modal.classList.add('open');
    document.body.style.overflow = 'hidden';
  };

  function close() {
    modal.classList.remove('open');
    document.body.style.overflow = '';
  }

  closeBtn.addEventListener('click', close);
  modal.addEventListener('click', function (e) { if (e.target === modal) close(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && modal.classList.contains('open')) close();
  });
}());