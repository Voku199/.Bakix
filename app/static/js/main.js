(function () {
  const canvas = document.getElementById('gradeChart');
  if (!canvas || typeof GRADE_DATA === 'undefined' || !GRADE_DATA.length) return;

  const s = getComputedStyle(document.documentElement);
  const get = (v) => s.getPropertyValue(v).trim();

  new Chart(canvas, {
    type: 'line',
    data: {
      datasets: GRADE_DATA.map((ds) => ({
        label:           ds.label,
        data:            ds.data,
        borderColor:     ds.borderColor,
        backgroundColor: ds.backgroundColor + '30',
        borderWidth:     2,
        pointRadius:     4,
        pointHoverRadius: 6,
        tension:         0.3,
        fill:            false,
      })),
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      scales: {
        x: {
          type: 'time',
          time: { unit: 'month', displayFormats: { month: 'MMM yy' } },
          grid:  { color: get('--border') },
          ticks: { color: get('--muted'), font: { family: "'Space Mono',monospace", size: 10 } },
        },
        y: {
          min:     0.5,
          max:     5.5,
          reverse: true,
          ticks: {
            stepSize: 1,
            color: get('--muted'),
            font: { family: "'Space Mono',monospace", size: 10 },
            callback: (v) => Number.isInteger(v) ? v : undefined,
          },
          grid: { color: get('--border') },
        },
      },
      plugins: {
        legend: {
          labels: {
            color:    get('--text'),
            font:     { family: "'Space Mono',monospace", size: 10 },
            boxWidth: 10,
            padding:  14,
          },
        },
        tooltip: {
          callbacks: {
            title: (items) => items[0].raw.x,
            label: (item) => ` ${item.dataset.label}: ${item.raw.y}`,
          },
        },
      },
    },
  });
})();
