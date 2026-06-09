/* ── Logo page-transition ──────────────────────────────────────────
   Intercepts the header-logo click, plays the full-screen curtain
   animation defined in base.css, then redirects to "/" once it ends.

   Timeline (kept in sync with the CSS @keyframes):
     0.0s–1.2s  logo glides in from the top-left to centre
     1.2s–1.8s  hold dead-centre
     1.8s–2.6s  fade out, then redirect                                 */
(function () {
  "use strict";

  var DURATION = 2600; // ms — MUST match the CSS animation length

  var logoLink = document.getElementById("header-logo");
  var overlay  = document.getElementById("logo-transition");
  if (!logoLink || !overlay) return;

  // Users who prefer reduced motion skip the show entirely.
  var prefersReduced = window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var playing = false;

  logoLink.addEventListener("click", function (e) {
    // Let modifier / middle clicks (open-in-new-tab etc.) behave normally.
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;

    e.preventDefault();              // Phase 1: block the immediate redirect
    if (playing) return;             // ignore double-clicks mid-animation
    playing = true;

    var target = logoLink.getAttribute("href") || "/";

    if (prefersReduced) {            // accessibility: no animation
      window.location.assign(target);
      return;
    }

    // Reveal the curtain and (re)start the CSS animations.
    overlay.hidden = false;
    void overlay.offsetWidth;        // force reflow so the animation restarts
    overlay.classList.add("is-playing");

    // Phase 3 — redirect exactly once, when the timeline completes.
    var redirected = false;
    function go() {
      if (redirected) return;
      redirected = true;
      window.location.assign(target);
    }

    // Primary trigger: the logo's own animationend (precise to the keyframes).
    var logoEl = overlay.querySelector(".logo-transition__logo");
    if (logoEl) {
      logoEl.addEventListener("animationend", go, { once: true });
    }
    // Safety net: fixed timeout in case animationend never fires.
    setTimeout(go, DURATION + 50);
  });
})();
