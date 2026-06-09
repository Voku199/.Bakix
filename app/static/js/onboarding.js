(function () {
  /* ── Step navigation ─────────────────────────────────────── */
  let currentStep = 0;
  const stepEls   = document.querySelectorAll('.step');
  const dotEls    = document.querySelectorAll('.stepper__item');

  window.goTo = function (n) {
    stepEls[currentStep].classList.remove('is-active');
    dotEls[currentStep].classList.remove('is-active');
    if (n > currentStep) dotEls[currentStep].classList.add('is-done');
    else                  dotEls[currentStep].classList.remove('is-done');
    currentStep = n;
    stepEls[currentStep].classList.add('is-active');
    dotEls[currentStep].classList.add('is-active');
  };

  /* ── Step 2: School search ───────────────────────────────── */
  const searchInput = document.getElementById('school-search-input');
  const dropdown    = document.getElementById('school-dropdown');
  const hiddenUrl   = document.getElementById('school_url');
  let   searchTimer = null;

  searchInput.addEventListener('input', function () {
    const val = this.value.trim();
    hiddenUrl.value = '';
    urlInput.classList.remove('is-valid', 'has-error');
    if (val.length < 2) { closeDropdown(); return; }
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => fetchSchools(val), 320);
  });

  searchInput.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeDropdown(); return; }
    if (e.key === 'ArrowDown') {
      const first = dropdown.querySelector('.school-option[data-url]');
      if (first) { first.focus(); e.preventDefault(); }
    }
  });

  async function fetchSchools(q) {
    renderDropdown(null); // loading state
    try {
      const r    = await fetch('/api/schools/search?q=' + encodeURIComponent(q));
      const data = await r.json();
      renderDropdown(data);
    } catch (_) {
      closeDropdown();
    }
  }

  function renderDropdown(schools) {
    dropdown.innerHTML = '';
    if (schools === null) {
      dropdown.innerHTML = '<li class="school-option school-option--info">Hledám…</li>';
      dropdown.classList.add('is-open');
      return;
    }
    if (!schools.length) {
      dropdown.innerHTML = '<li class="school-option school-option--info">Žádná škola nenalezena — zkuste URL ručně</li>';
      dropdown.classList.add('is-open');
      return;
    }
    dropdown.innerHTML = schools.map(s =>
      `<li class="school-option" role="option" tabindex="0"
           data-url="${escAttr(s.url)}"
           data-name="${escAttr(s.name)}">
        <span class="school-option__name">${escHtml(s.name)}</span>
        <span class="school-option__city">${escHtml(s.city)}</span>
       </li>`
    ).join('');
    dropdown.classList.add('is-open');

    dropdown.querySelectorAll('.school-option[data-url]').forEach(el => {
      el.addEventListener('click',   () => selectSchool(el));
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { selectSchool(el); e.preventDefault(); }
        if (e.key === 'ArrowDown') { el.nextElementSibling?.focus(); e.preventDefault(); }
        if (e.key === 'ArrowUp')   {
          (el.previousElementSibling ? el.previousElementSibling : searchInput).focus();
          e.preventDefault();
        }
        if (e.key === 'Escape') closeDropdown();
      });
    });
  }

  function selectSchool(el) {
    const url  = el.dataset.url;
    const name = el.dataset.name;
    searchInput.value = name;
    hiddenUrl.value   = url;
    closeDropdown();
    // Schools from the directory are already validated — go straight to credentials
    setTimeout(() => goTo(2), 120);
  }

  function closeDropdown() {
    dropdown.classList.remove('is-open');
    dropdown.innerHTML = '';
  }

  document.addEventListener('click', function (e) {
    if (!e.target.closest('.school-search-wrap')) closeDropdown();
  });

  /* ── Step 2: Manual URL validation ──────────────────────── */
  const urlInput    = document.getElementById('school-url-input');
  const urlStatus   = document.getElementById('url-status');
  const btnValidate = document.getElementById('btn-validate');

  urlInput.addEventListener('input', function () {
    hiddenUrl.value = '';
    urlInput.classList.remove('is-valid', 'has-error');
    urlStatus.style.display = 'none';
    // Clear search selection when user edits manual URL
    searchInput.value = '';
  });

  urlInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); runValidation(); }
  });

  btnValidate.addEventListener('click', function () {
    // If URL already confirmed by school search — just advance
    if (hiddenUrl.value) { goTo(2); return; }
    runValidation();
  });

  async function runValidation() {
    const raw = urlInput.value.trim();
    if (!raw) {
      urlInput.classList.add('has-error');
      urlInput.focus();
      setStatus('fail', '✗ Zadejte adresu školy.');
      return;
    }

    urlInput.classList.remove('is-valid', 'has-error');
    urlStatus.style.display  = 'none';
    btnValidate.disabled     = true;
    btnValidate.classList.add('btn--loading');
    btnValidate.textContent  = 'Ověřuji';

    try {
      const resp = await fetch('/api/validate-school?url=' + encodeURIComponent(raw));
      if (!resp.ok) throw new Error('http ' + resp.status);
      const data = await resp.json();

      if (data.valid) {
        hiddenUrl.value = data.url;
        urlInput.classList.add('is-valid');
        setStatus('ok', '✓ Server nalezen: ' + data.url);
        setTimeout(() => goTo(2), 500);
      } else {
        urlInput.classList.add('has-error');
        setStatus('fail', '✗ Na zadané adrese nebyl nalezen server Bakaláře. Zkontrolujte URL.');
      }
    } catch (_) {
      urlInput.classList.add('has-error');
      setStatus('fail', '✗ Nepodařilo se připojit. Zkontrolujte URL nebo síťové připojení.');
    } finally {
      btnValidate.disabled = false;
      btnValidate.classList.remove('btn--loading');
      btnValidate.textContent = 'Ověřit a pokračovat →';
    }
  }

  function setStatus(type, msg) {
    urlStatus.className     = 'url-status url-status--' + type;
    urlStatus.textContent   = msg;
    urlStatus.style.display = 'flex';
  }

  /* ── Form submit (step 3) ────────────────────────────────── */
  const form      = document.getElementById('login-form');
  const errorBox  = document.getElementById('login-error');
  const btnSubmit = document.getElementById('btn-connect');
  let   submitting = false;

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (submitting) return;

    errorBox.style.display = 'none';

    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;

    if (!username || !password) {
      showLoginError('Vyplňte uživatelské jméno i heslo.');
      return;
    }

    submitting = true;
    setSubmitLoading(true);

    const body = new FormData();
    body.append('school_url', hiddenUrl.value);
    body.append('username',   username);
    body.append('password',   password);

    let succeeded = false;

    try {
      const resp = await fetch(form.action, { method: 'POST', body });

      if (resp.redirected) {
        succeeded = true;
        try { localStorage.setItem('bakix-school', hiddenUrl.value); } catch (_) {}
        window.location.href = resp.url;
        return;
      }

      if (!resp.ok) {
        let msg;
        try {
          const data = await resp.json();
          msg = data.detail || data.error || null;
        } catch (_) {
          msg = null;
        }
        if (!msg) {
          msg = resp.status >= 500
            ? `Chyba serveru (${resp.status}). Zkuste to znovu.`
            : `Přihlášení selhalo (${resp.status}).`;
        } else if (resp.status >= 500) {
          msg = `Chyba serveru: ${msg}`;
        }
        showLoginError(msg);
        return;
      }

      showLoginError('Neočekávaná odpověď serveru. Zkuste to znovu.');
    } catch (_) {
      showLoginError('Nepodařilo se spojit se serverem. Zkuste to znovu.');
    } finally {
      submitting = false;
      if (!succeeded) setSubmitLoading(false);
    }
  });

  function setSubmitLoading(on) {
    btnSubmit.disabled = on;
    if (on) {
      btnSubmit.classList.add('btn--loading');
      btnSubmit.textContent = 'Připojuji';
    } else {
      btnSubmit.classList.remove('btn--loading');
      btnSubmit.textContent = 'Připojit se';
    }
  }

  function showLoginError(msg) {
    errorBox.textContent   = msg;
    errorBox.style.display = 'block';
  }

  /* ── Utils ───────────────────────────────────────────────── */
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escAttr(s) { return escHtml(s); }
})();
