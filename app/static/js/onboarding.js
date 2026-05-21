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

  /* ── Step 2: URL validation ──────────────────────────────── */
  const urlInput    = document.getElementById('school-url-input');
  const hiddenUrl   = document.getElementById('school_url');
  const urlStatus   = document.getElementById('url-status');
  const btnValidate = document.getElementById('btn-validate');

  // Reset validation if the user edits the URL after a successful check
  urlInput.addEventListener('input', function () {
    hiddenUrl.value = '';
    urlInput.classList.remove('is-valid', 'has-error');
    urlStatus.style.display = 'none';
  });

  urlInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); runValidation(); }
  });

  btnValidate.addEventListener('click', runValidation);

  async function runValidation() {
    const raw = urlInput.value.trim();

    if (!raw) {
      urlInput.classList.add('has-error');
      urlInput.focus();
      setStatus('fail', '✗ Zadejte adresu školy.');
      return;
    }

    // Start loading
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
        // Short pause so the user sees the green confirmation, then advance
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
  let   submitting = false;  // guard against concurrent submissions

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

    // Send only the three required fields — nothing else from the form
    const body = new FormData();
    body.append('school_url', hiddenUrl.value);
    body.append('username',   username);
    body.append('password',   password);

    let succeeded = false;

    try {
      const resp = await fetch(form.action, { method: 'POST', body });

      if (resp.redirected) {
        succeeded = true;  // keep spinner active during navigation
        try { localStorage.setItem('bakix-school', hiddenUrl.value); } catch (_) {}
        window.location.href = resp.url;
        return;
      }

      if (!resp.ok) {
        // Attempt to parse the error JSON; fall back to a generic message
        // if the server returned a non-JSON body (e.g. a 500 HTML error page)
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

      // 2xx without redirect — unexpected from this endpoint
      showLoginError('Neočekávaná odpověď serveru. Zkuste to znovu.');
    } catch (_) {
      showLoginError('Nepodařilo se spojit se serverem. Zkuste to znovu.');
    } finally {
      submitting = false;
      // Leave the spinner on while the browser navigates to the dashboard
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
})();

