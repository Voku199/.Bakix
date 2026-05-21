(function () {
  const LS_SCHOOL   = 'bakix-school';
  const LS_USERNAME = 'bakix-username';

  const schoolDisplay = document.getElementById('school-display');
  const schoolInput   = document.getElementById('school_url');
  const schoolRow     = document.getElementById('school-row');
  const schoolExpand  = document.getElementById('school-expand');
  const usernameInput = document.getElementById('username');
  const rememberCb    = document.getElementById('remember-me');
  const form          = document.getElementById('login-form');
  const errorBox      = document.getElementById('login-error');
  const btnSubmit     = document.getElementById('btn-submit');

  /* ── Restore from localStorage ─────────────────────────────── */
  const savedSchool   = localStorage.getItem(LS_SCHOOL)   || '';
  const savedUsername = localStorage.getItem(LS_USERNAME) || '';

  if (savedSchool) {
    schoolInput.value   = savedSchool;
    schoolDisplay.textContent = savedSchool;
  } else {
    schoolDisplay.textContent = 'Zadat adresu školy';
    openSchoolField();
  }

  if (savedUsername) {
    usernameInput.value = savedUsername;
  }

  /* ── School row toggle ──────────────────────────────────────── */
  function openSchoolField() {
    schoolExpand.classList.add('is-open');
    schoolRow.style.display = 'none';
  }

  schoolRow.addEventListener('click', openSchoolField);
  schoolRow.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSchoolField(); }
  });

  schoolInput.addEventListener('blur', function () {
    const v = schoolInput.value.trim();
    if (v) {
      schoolDisplay.textContent = v;
      schoolExpand.classList.remove('is-open');
      schoolRow.style.display = '';
    }
  });

  /* ── Form submit ────────────────────────────────────────────── */
  let submitting = false;

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    if (submitting) return;

    errorBox.style.display = 'none';

    let schoolUrl = schoolInput.value.trim().replace(/\/+$/, '');
    const username = usernameInput.value.trim();
    const password = document.getElementById('password').value;

    if (!schoolUrl) {
      showError('Zadejte adresu školy.');
      openSchoolField();
      schoolInput.focus();
      return;
    }

    if (!username || !password) {
      showError('Vyplňte uživatelské jméno i heslo.');
      return;
    }

    if (!schoolUrl.startsWith('http://') && !schoolUrl.startsWith('https://')) {
      schoolUrl = 'https://' + schoolUrl;
    }

    submitting = true;
    setLoading(true);

    const body = new FormData();
    body.append('school_url', schoolUrl);
    body.append('username',   username);
    body.append('password',   password);

    let succeeded = false;

    try {
      const resp = await fetch('/login', { method: 'POST', body });

      if (resp.redirected) {
        succeeded = true;
        if (rememberCb.checked) {
          try {
            localStorage.setItem(LS_SCHOOL,   schoolUrl);
            localStorage.setItem(LS_USERNAME, username);
          } catch (_) {}
        } else {
          localStorage.removeItem(LS_SCHOOL);
          localStorage.removeItem(LS_USERNAME);
        }
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
            : `Přihlášení selhalo — zkontrolujte jméno a heslo.`;
        } else if (resp.status >= 500) {
          msg = `Chyba serveru: ${msg}`;
        }
        showError(msg);
        return;
      }

      showError('Neočekávaná odpověď serveru. Zkuste to znovu.');
    } catch (_) {
      showError('Nepodařilo se spojit se serverem. Zkuste to znovu.');
    } finally {
      submitting = false;
      if (!succeeded) setLoading(false);
    }
  });

  function setLoading(on) {
    btnSubmit.disabled = on;
    btnSubmit.classList.toggle('is-loading', on);
    btnSubmit.textContent = on ? 'Přihlašuji' : 'Přihlásit se';
  }

  function showError(msg) {
    errorBox.textContent   = msg;
    errorBox.style.display = 'block';
    errorBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
})();

