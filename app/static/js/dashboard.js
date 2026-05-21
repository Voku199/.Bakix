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
        const t = document.createElement('div'); t.className = 'msg-item__text'; t.textContent = m.Text;
        div.appendChild(t);
      }
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
      name.textContent = (s.Subject && s.Subject.Name) || '—';
      const abbrev = document.createElement('span');
      abbrev.className = 'subject__abbrev';
      const abbrevStr = ((s.Subject && s.Subject.Abbrev) || '').trim();
      abbrev.textContent = abbrevStr ? ' (' + abbrevStr + ')' : '';
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
        mv.className = 'mv mv--' + (parseInt(m.MarkText) || 'other');
        mv.textContent = m.MarkText || '?';
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
      setCard('events-body', '<p class="card-loading">Načítám…</p>');
      fetch(url).then(function (r) { return r.json(); })
        .then(renderTimetable)
        .catch(function () { cardErr('events-body', 'Síťová chyba.'); });
    });
  });
});

