/* ═══════════════════════════════════════════════════════════════
   Bakix Motion — Apple-grade motion system (JS half)
   ----------------------------------------------------------------
   Drop-in module, zero dependencies, zero markup changes.
   Include in base.html after base.js:

     <link rel="stylesheet" href=".../style/motion.css">
     <script src=".../js/motion.js"></script>

   What it auto-wires on every page:
     1.  Page-load choreography — header + content cascade in with a
         staggered rise/de-blur; below-fold cards reveal on scroll.
     2.  Skeleton → data morph — dashboard card bodies are observed
         with MutationObserver; fresh rows stagger in, no app-code
         changes needed.
     3.  Count-up averages — grade averages roll from 0.00 to value.
     4.  iOS press physics — buttons/rows scale down on press and
         spring back on release (WAAPI, so no CSS conflicts).
     5.  Card tilt + specular glare — subtle Apple-TV style 3D tilt
         following the pointer (desktop, fine pointers only).
     6.  Accordion height animation — marks detail expands/collapses
         with real height + per-row cascade.
     7.  Modal origin morph — settings / message / compose panels
         grow out of the exact point you tapped.
     8.  Frosted header — translucent glass + shadow once scrolled.
     9.  Theme switch — circular reveal wipe from the toggle button
         (View Transition API, graceful fallback).
    10.  Chat bubbles — iMessage-style spring pop for new messages.

   Respects prefers-reduced-motion: the module disables itself
   entirely and the app behaves exactly as before.

   Public API:  window.BakixMotion = { reveal, stagger, countUp }
   Opt-out:     add data-bm-skip to any element the entrance
                choreography should leave alone.
   ═══════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  if (!('animate' in Element.prototype)) return; /* very old browsers */

  var html = document.documentElement;
  html.classList.add('bm-js');

  var EASE_OUT    = 'cubic-bezier(.22,1,.36,1)';
  var EASE_SPRING = 'cubic-bezier(.34,1.4,.44,1)';

  /* ── Shared primitives ─────────────────────────────────── */

  function reveal(el, delay) {
    el.animate(
      [
        { opacity: 0, transform: 'translateY(16px) scale(.985)', filter: 'blur(5px)' },
        { opacity: 1, transform: 'none', filter: 'blur(0px)' },
      ],
      { duration: 650, delay: delay || 0, easing: EASE_OUT, fill: 'backwards' }
    );
  }

  function stagger(nodes, opts) {
    opts = opts || {};
    var els = Array.prototype.slice.call(nodes)
      .filter(function (n) { return n.nodeType === 1; })
      .slice(0, opts.max || 14);
    els.forEach(function (el, i) {
      el.animate(
        [
          { opacity: 0, transform: 'translateY(' + (opts.y != null ? opts.y : 10) + 'px) scale(.99)', filter: 'blur(3px)' },
          { opacity: 1, transform: 'none', filter: 'blur(0px)' },
        ],
        { duration: opts.dur || 460, delay: i * (opts.step != null ? opts.step : 42), easing: EASE_OUT, fill: 'backwards' }
      );
    });
  }

  /* Rolls "1,50"-style numeric text from 0 up to its value. */
  function countUp(el, dur) {
    var txt = el.textContent.trim();
    var m = txt.match(/^(\d+)([.,])(\d+)$/);
    if (!m) return;
    var target = parseFloat(m[1] + '.' + m[3]);
    var dec = m[3].length, sep = m[2];
    var t0 = performance.now();
    dur = dur || 900;
    (function frame(t) {
      var p = Math.min(1, (t - t0) / dur);
      var e = 1 - Math.pow(1 - p, 4);
      el.textContent = (target * e).toFixed(dec).replace('.', sep);
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = txt;
    })(t0);
  }

  /* ── 1 · Page-load choreography ────────────────────────── */
  /* Runs synchronously before first paint (script sits at the end
     of <body>), so fill:'backwards' hides targets from frame one. */
  (function entrance() {
    var header = document.querySelector('.header');
    if (header) {
      header.animate(
        [{ opacity: 0, transform: 'translateY(-10px)' }, { opacity: 1, transform: 'none' }],
        { duration: 520, easing: EASE_OUT, fill: 'backwards' }
      );
    }

    var main = document.querySelector('main.container');
    if (!main) return;

    var targets = [];
    Array.prototype.forEach.call(main.children, function (el) {
      if (el.classList.contains('dash-grid')) {
        Array.prototype.push.apply(targets, el.children);
      } else {
        targets.push(el);
      }
    });
    targets = targets.filter(function (el) {
      /* offsetParent is null for display:none and position:fixed
         (overlays, FAB) — exactly what we want to skip */
      return el.offsetParent !== null && !el.hasAttribute('data-bm-skip');
    });

    var vh = window.innerHeight;
    var below = [];
    var i = 0;
    targets.forEach(function (el) {
      if (el.getBoundingClientRect().top < vh * 0.92) reveal(el, 55 * i++);
      else below.push(el);
    });

    if (below.length && 'IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          io.unobserve(en.target);
          en.target.style.opacity = '';
          reveal(en.target);
        });
      }, { threshold: 0.08 });
      below.forEach(function (el) {
        el.style.opacity = '0';
        io.observe(el);
      });
    }

    var fab = document.querySelector('.ai-fab');
    if (fab) {
      fab.animate(
        [
          { opacity: 0, transform: 'scale(.4) translateY(18px)' },
          { opacity: 1, transform: 'scale(1.07) translateY(-2px)', offset: 0.7 },
          { opacity: 1, transform: 'none' },
        ],
        { duration: 620, delay: 480, easing: EASE_OUT, fill: 'backwards' }
      );
    }
  })();

  /* ── 2+3 · Skeleton → data morph + count-up averages ───── */
  document.addEventListener('DOMContentLoaded', function () {

    ['events-body', 'homeworks-body', 'komens-body', 'marks-body',
     'my-projects-body', 'calc-result'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var pending = false;
      new MutationObserver(function () {
        if (pending) return;
        pending = true;
        /* coalesce innerHTML='' + appendChild into one pass */
        requestAnimationFrame(function () {
          pending = false;
          var kids = Array.prototype.slice.call(el.children);
          if (!kids.length) return;
          if (kids.some(function (k) { return k.classList.contains('skel-rows'); })) return;
          /* single wrapper (e.g. .projects-grid) → animate its rows */
          var rows = (kids.length === 1 && kids[0].children.length > 1) ? kids[0].children : kids;
          stagger(rows);
          el.querySelectorAll('.marks-sum-row__avg').forEach(function (b) {
            if (b.dataset.bmCounted) return;
            b.dataset.bmCounted = '1';
            countUp(b);
          });
        });
      }).observe(el, { childList: true });
    });

    /* Greeting arrives async — soft de-blur when it lands */
    var greet = document.getElementById('dash-greeting');
    if (greet) {
      new MutationObserver(function () {
        if (greet.dataset.bmDone) return;
        greet.dataset.bmDone = '1';
        greet.animate(
          [
            { opacity: 0, transform: 'translateY(6px)', filter: 'blur(4px)' },
            { opacity: 1, transform: 'none', filter: 'blur(0px)' },
          ],
          { duration: 520, easing: EASE_OUT }
        );
      }).observe(greet, { childList: true });
    }

    /* Cards that pop into existence via style.display changes */
    ['calc-card', 'ai-summary-card', 'push-prompt-row', 'ios-prompt-row'].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      var wasHidden = el.style.display === 'none';
      new MutationObserver(function () {
        var hidden = el.style.display === 'none';
        if (wasHidden && !hidden) reveal(el);
        wasHidden = hidden;
      }).observe(el, { attributes: true, attributeFilter: ['style'] });
    });
  });

  /* ── 4 · iOS press physics (WAAPI — no CSS conflicts) ──── */
  (function pressFeedback() {
    var SEL = '.btn, .msg-item, .project-link, .chart-dot, .ai-suggestion-btn,' +
              '.ai-model-opt, .chat-mode-btn, .marks-sum-row__header, .ai-fab';
    var active = []; /* [el, anim] pairs currently pressed */

    document.addEventListener('pointerdown', function (e) {
      var el = e.target.closest && e.target.closest(SEL);
      if (!el) return;
      var deep = el.classList.contains('ai-fab') ? 0.9
               : el.classList.contains('marks-sum-row__header') ? 0.99
               : 0.96;
      var anim = el.animate({ scale: [1, deep] },
        { duration: 90, easing: 'ease-out', fill: 'forwards' });
      active.push([el, anim, deep]);
      if (el.matches('.btn--primary, .ai-fab') && navigator.vibrate) {
        try { navigator.vibrate(4); } catch (_) { /* ignore */ }
      }
    }, { capture: true, passive: true });

    function releaseAll() {
      while (active.length) {
        var pair = active.pop();
        pair[1].cancel();
        pair[0].animate({ scale: [pair[2], 1] },
          { duration: 380, easing: EASE_SPRING });
      }
    }
    window.addEventListener('pointerup', releaseAll, { capture: true, passive: true });
    window.addEventListener('pointercancel', releaseAll, { capture: true, passive: true });
  })();

  /* ── 5 · Card tilt + specular glare (desktop only) ─────── */
  if (window.matchMedia('(hover: hover) and (pointer: fine)').matches) {
    document.addEventListener('DOMContentLoaded', function () {
      var TILT = 1.7; /* deg — keep it whisper-subtle */
      document.querySelectorAll('.dash-grid .card, .chart-panel').forEach(function (card) {
        card.classList.add('bm-tiltable');
        var glare = document.createElement('span');
        glare.className = 'bm-glare';
        glare.setAttribute('aria-hidden', 'true');
        card.appendChild(glare);

        var rx = 0, ry = 0, tx = 0, ty = 0, raf = 0, hover = false;
        function loop() {
          rx += (tx - rx) * 0.12;
          ry += (ty - ry) * 0.12;
          card.style.transform =
            'perspective(900px) rotateX(' + rx.toFixed(3) + 'deg) rotateY(' + ry.toFixed(3) + 'deg)';
          if (hover || Math.abs(rx) > 0.01 || Math.abs(ry) > 0.01) {
            raf = requestAnimationFrame(loop);
          } else {
            card.style.transform = '';
            raf = 0;
          }
        }
        card.addEventListener('pointerenter', function (e) {
          if (e.pointerType !== 'mouse') return;
          hover = true;
          if (!raf) raf = requestAnimationFrame(loop);
        });
        card.addEventListener('pointermove', function (e) {
          if (e.pointerType !== 'mouse') return;
          var r = card.getBoundingClientRect();
          var px = (e.clientX - r.left) / r.width;
          var py = (e.clientY - r.top) / r.height;
          ty = (px - 0.5) * 2 * TILT;
          tx = -(py - 0.5) * 2 * TILT;
          card.style.setProperty('--bm-mx', (px * 100).toFixed(1) + '%');
          card.style.setProperty('--bm-my', (py * 100).toFixed(1) + '%');
        });
        card.addEventListener('pointerleave', function () {
          hover = false;
          tx = 0; ty = 0;
          if (!raf) raf = requestAnimationFrame(loop);
        });
      });
    });
  }

  /* ── 6 · Marks accordion height animation ──────────────── */
  document.addEventListener('DOMContentLoaded', function () {
    var marksBody = document.getElementById('marks-body');
    if (!marksBody) return;
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        var row = m.target;
        if (!(row instanceof Element) || !row.classList.contains('marks-sum-row')) return;
        var detail = row.querySelector('.marks-detail');
        if (!detail) return;
        var isOpen  = row.classList.contains('open');
        var wasOpen = !!(m.oldValue && /\bopen\b/.test(m.oldValue));
        if (isOpen === wasOpen) return;
        var h = detail.scrollHeight;
        if (isOpen) {
          detail.animate({ height: ['0px', h + 'px'] }, { duration: 420, easing: EASE_OUT });
          stagger(detail.children, { y: 6, dur: 300, step: 22, max: 20 });
        } else {
          detail.animate({ height: [h + 'px', '0px'] }, { duration: 300, easing: EASE_OUT });
        }
      });
    }).observe(marksBody, {
      subtree: true, attributes: true,
      attributeFilter: ['class'], attributeOldValue: true,
    });
  });

  /* ── 7 · Modals grow out of the tap point ──────────────── */
  (function originMorph() {
    var lastTap = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    document.addEventListener('pointerdown', function (e) {
      lastTap = { x: e.clientX, y: e.clientY };
    }, { capture: true, passive: true });

    document.addEventListener('DOMContentLoaded', function () {
      [
        ['settings-overlay', '.settings-panel'],
        ['msg-modal',        '.msg-modal__panel'],
        ['compose-modal',    '.compose-modal__panel'],
      ].forEach(function (pair) {
        var overlay = document.getElementById(pair[0]);
        if (!overlay) return;
        var panel = overlay.querySelector(pair[1]);
        if (!panel) return;
        new MutationObserver(function () {
          if (!overlay.classList.contains('open')) return;
          var r = panel.getBoundingClientRect();
          var ox = Math.max(0, Math.min(r.width,  lastTap.x - r.left));
          var oy = Math.max(0, Math.min(r.height, lastTap.y - r.top));
          panel.style.transformOrigin = ox.toFixed(0) + 'px ' + oy.toFixed(0) + 'px';
          if (pair[0] === 'settings-overlay') {
            stagger(panel.querySelectorAll('.settings-section'), { y: 8, dur: 360, step: 28 });
          }
        }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
      });
    });
  })();

  /* ── 8 · Frosted header on scroll ──────────────────────── */
  (function headerGlass() {
    var update = function () {
      html.classList.toggle('bm-scrolled', window.scrollY > 6);
    };
    window.addEventListener('scroll', update, { passive: true });
    update();
  })();

  /* ── 9 · Theme switch: circular reveal wipe ────────────── */
  (function themeReveal() {
    if (!document.startViewTransition) return;
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    var bypass = false;
    document.addEventListener('click', function (e) {
      if (bypass || !btn.contains(e.target)) return;
      e.stopPropagation(); /* base.js's own handler runs via btn.click() below */
      var r = btn.getBoundingClientRect();
      var x = r.left + r.width / 2;
      var y = r.top + r.height / 2;
      var maxR = Math.hypot(
        Math.max(x, window.innerWidth - x),
        Math.max(y, window.innerHeight - y)
      );
      var vt = document.startViewTransition(function () {
        bypass = true;
        btn.click();
        bypass = false;
      });
      vt.ready.then(function () {
        html.animate(
          { clipPath: ['circle(0px at ' + x + 'px ' + y + 'px)',
                       'circle(' + maxR + 'px at ' + x + 'px ' + y + 'px)'] },
          { duration: 650, easing: EASE_OUT, pseudoElement: '::view-transition-new(root)' }
        );
      }).catch(function () { /* transition skipped — theme still applied */ });
    }, true);
  })();

  /* ── 10 · Chat bubbles: iMessage spring pop ────────────── */
  document.addEventListener('DOMContentLoaded', function () {
    var thread = document.getElementById('ai-thread');
    if (!thread) return;
    new MutationObserver(function (muts) {
      muts.forEach(function (m) {
        m.addedNodes.forEach(function (n) {
          if (n.nodeType !== 1) return;
          n.animate(
            [
              { opacity: 0, transform: 'translateY(10px) scale(.9)' },
              { opacity: 1, transform: 'none' },
            ],
            { duration: 420, easing: EASE_SPRING }
          );
        });
      });
    }).observe(thread, { childList: true });
  });

  /* ── Public API ─────────────────────────────────────────── */
  window.BakixMotion = { reveal: reveal, stagger: stagger, countUp: countUp };
})();
