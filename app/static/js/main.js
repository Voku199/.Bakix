(function () {
  'use strict';

  const canvas = document.getElementById('gradeChart');
  if (!canvas || typeof GRADE_DATA === 'undefined' || !GRADE_DATA.length) return;

  // ── Priority sort: Čeština → Matematika → rest (cs-locale alphabetical) ──
  function _prio(label) {
    const l = label.toLowerCase();
    if (/češt|český|^čj$|^cj$/.test(l)) return 0;
    if (/matem|^ma$/.test(l))           return 1;
    return 2;
  }
  const ds = [...GRADE_DATA].sort((a, b) => {
    const d = _prio(a.label) - _prio(b.label);
    return d !== 0 ? d : a.label.localeCompare(b.label, 'cs');
  });
  const N = ds.length;

  // ── CSS token helper ─────────────────────────────────────────────────────
  const ct  = getComputedStyle(document.documentElement);
  const tok = (v) => ct.getPropertyValue(v).trim();

  // ── Chart ────────────────────────────────────────────────────────────────
  const chart = new Chart(canvas, {
    type: 'line',
    data: {
      datasets: ds.map((d, i) => ({
        label:                d.label,
        data:                 d.data,
        borderColor:          d.borderColor,
        backgroundColor:      d.borderColor + '22',
        borderWidth:          2.5,
        pointRadius:          5,
        pointHoverRadius:     8,
        pointBackgroundColor: d.borderColor,
        tension:              0.35,
        fill:                 false,
        hidden:               i !== 0,
      })),
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           { duration: 450 },
      scales: {
        x: {
          type: 'time',
          time: { unit: 'month', displayFormats: { month: 'MMM yy' } },
          grid:  { color: tok('--border') },
          ticks: {
            color:         tok('--muted'),
            font:          { family: "'Space Mono',monospace", size: 10 },
            maxRotation:   0,
            maxTicksLimit: 7,
          },
        },
        y: {
          min:     0.5,
          max:     5.5,
          reverse: true,
          ticks: {
            stepSize: 1,
            color:    tok('--muted'),
            font:     { family: "'Space Mono',monospace", size: 10 },
            callback: (v) => Number.isInteger(v) ? v : undefined,
          },
          grid: { color: tok('--border') },
        },
      },
      plugins: {
        legend:  { display: false },   // replaced by custom nav
        tooltip: {
          callbacks: {
            title: (items) => items[0].raw.x,
            label: (item)  => ` ${item.dataset.label}: ${item.raw.y}`,
          },
        },
      },
    },
  });

  // ── Nav DOM refs ─────────────────────────────────────────────────────────
  const labelEl   = document.getElementById('chart-subject-label');
  const dotsEl    = document.getElementById('chart-dots');
  const prevBtn   = document.getElementById('chart-prev');
  const nextBtn   = document.getElementById('chart-next');
  const progBar   = document.getElementById('chart-progress-bar');

  // Build one dot button per dataset
  const dotEls = ds.map((d, i) => {
    const btn = document.createElement('button');
    btn.className   = 'chart-dot';
    btn.title       = d.label;
    btn.setAttribute('aria-label', d.label);
    btn.addEventListener('click', () => { onUserInteraction(); jumpTo(i); });
    if (dotsEl) dotsEl.appendChild(btn);
    return btn;
  });

  // ── Cycle state ──────────────────────────────────────────────────────────
  let cur         = 0;
  let cycleTimer  = null;
  let resumeTimer = null;
  let autoPaused  = false;

  const CYCLE_MS  = 60_000;   // 1 min per subject
  const RESUME_MS = 300_000;  // 5 min pause after manual interaction

  // ── Progress bar helpers ─────────────────────────────────────────────────
  function startProgress() {
    if (!progBar) return;
    progBar.style.animation = 'none';
    void progBar.offsetWidth;                  // force reflow to restart
    progBar.style.animation = 'chart-fill ' + (CYCLE_MS / 1000) + 's linear forwards';
  }

  function stopProgress() {
    if (!progBar) return;
    progBar.style.animationPlayState = 'paused';
  }

  // ── Core ─────────────────────────────────────────────────────────────────
  function jumpTo(idx) {
    cur = ((idx % N) + N) % N;
    for (let i = 0; i < N; i++) chart.setDatasetVisibility(i, i === cur);
    chart.update();
    syncUI();
  }

  function syncUI() {
    const color = ds[cur].borderColor;

    if (labelEl) {
      labelEl.textContent = ds[cur].label;
      labelEl.style.color = color;
    }
    dotEls.forEach((d, i) => {
      const active = i === cur;
      d.classList.toggle('chart-dot--active', active);
      d.style.background = active ? color : '';
    });
    if (prevBtn) prevBtn.disabled = N <= 1;
    if (nextBtn) nextBtn.disabled = N <= 1;
  }

  function advance() {
    jumpTo(cur + 1);
    scheduleCycle();
    startProgress();
  }

  function scheduleCycle() {
    clearTimeout(cycleTimer);
    if (N > 1) cycleTimer = setTimeout(advance, CYCLE_MS);
  }

  function onUserInteraction() {
    clearTimeout(cycleTimer);
    clearTimeout(resumeTimer);
    autoPaused = true;
    stopProgress();
    resumeTimer = setTimeout(() => {
      autoPaused = false;
      scheduleCycle();
      startProgress();
    }, RESUME_MS);
  }

  // ── Button listeners ─────────────────────────────────────────────────────
  if (prevBtn) prevBtn.addEventListener('click', () => { onUserInteraction(); jumpTo(cur - 1); });
  if (nextBtn) nextBtn.addEventListener('click', () => { onUserInteraction(); jumpTo(cur + 1); });

  // ── Init ─────────────────────────────────────────────────────────────────
  jumpTo(0);
  if (N > 1) { scheduleCycle(); startProgress(); }
})();
