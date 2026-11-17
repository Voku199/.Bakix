/*
 * Jediný skript Bakixu. Všechno funguje i bez něj; tady jsou jen tři drobná vylepšení:
 *   1. tlačítko „Zkopírovat“ (data-copy="#id"),
 *   2. našeptávání škol během psaní (data-school-search="url"),
 *   3. potvrzení nevratné akce (data-confirm="otázka").
 * CSP povoluje jen skripty ze 'self', proto žádný inline kód v šablonách.
 */
(function () {
  "use strict";

  // 1. kopírování
  document.querySelectorAll("[data-copy]").forEach(function (button) {
    var target = document.querySelector(button.dataset.copy);
    if (!target || !navigator.clipboard) return;
    button.hidden = false;
    var label = button.querySelector("span");
    var original = label ? label.textContent : "";
    button.addEventListener("click", function () {
      navigator.clipboard.writeText(target.value || target.textContent).then(function () {
        if (!label) return;
        label.textContent = button.dataset.copied || "Zkopírováno";
        setTimeout(function () { label.textContent = original; }, 2000);
      });
    });
  });

  // 2. našeptávání škol
  var search = document.querySelector("[data-school-search]");
  if (search && window.fetch) {
    var results = document.querySelector(search.dataset.results);
    var submit = search.form && search.form.querySelector("[data-hide-with-js]");
    if (submit) submit.hidden = true;
    var timer = null;
    var latest = 0;
    search.addEventListener("input", function () {
      clearTimeout(timer);
      var query = search.value.trim();
      if (query.length < 2) { results.textContent = ""; return; }
      timer = setTimeout(function () {
        var id = ++latest;
        fetch(search.dataset.schoolSearch + "?q=" + encodeURIComponent(query), {
          headers: { Accept: "text/html" },
          credentials: "same-origin"
        })
          .then(function (response) { return response.ok ? response.text() : ""; })
          .then(function (html) {
            // HTML je kousek naší vlastní šablony (Jinja escapuje), ne data od uživatele.
            if (id === latest) results.innerHTML = html;
          })
          .catch(function () { if (submit) submit.hidden = false; });
      }, 250);
    });
  }

  // 3. potvrzení
  document.querySelectorAll("[data-confirm]").forEach(function (button) {
    button.addEventListener("click", function (event) {
      if (!window.confirm(button.dataset.confirm)) event.preventDefault();
    });
  });
})();
