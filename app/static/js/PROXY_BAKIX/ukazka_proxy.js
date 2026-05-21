// =============================================================================
// PROXY_BAKIX – ukázkové volání API
// Proxy běží na: https://bakix-proxy.onrender.com/
//
// BEZPEČNOSTNÍ POZNÁMKA: Server běží přes HTTP (ne HTTPS). Heslo a token
// putují po síti nešifrovaně. Pro produkci použij HTTPS + ngrok nebo Cloudflare.
// =============================================================================

const PROXY_BASE = "https://bakix-proxy.onrender.com/";

// Token a school_url ukládáme pouze do paměti (ne localStorage),
// aby se vymazaly při zavření záložky.
let _session = {
  accessToken: null,
  schoolUrl:   null,
};


// -----------------------------------------------------------------------------
// Interní helper – všechny requesty jdou přes tuto funkci
// -----------------------------------------------------------------------------
async function _proxyFetch(path, options = {}) {
  const url = `${PROXY_BASE}${path}`;

  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  // Přidej auth hlavičky pokud je session aktivní
  if (_session.accessToken) {
    headers["Authorization"] = `Bearer ${_session.accessToken}`;
  }
  if (_session.schoolUrl) {
    headers["X-School-Url"] = _session.schoolUrl;
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  const data = await response.json();

  if (!response.ok) {
    const err = new Error(data.error || `HTTP ${response.status}`);
    err.status = response.status;
    err.detail = data.detail || null;
    throw err;
  }

  return data;
}


// =============================================================================
// 1. LOGIN
// Zavolej jako první – uloží token do _session
// =============================================================================
async function bakixLogin(schoolUrl, username, password) {
  const data = await _proxyFetch("/auth/login", {
    method: "POST",
    body: JSON.stringify({ school_url: schoolUrl, username, password }),
  });

  _session.accessToken = data.access_token;
  _session.schoolUrl   = schoolUrl;

  return data.access_token;
}


// =============================================================================
// 2. DASHBOARD – volej až po úspěšném login()
// =============================================================================

async function getHomeworksCount() {
  const data = await _proxyFetch("/dashboard/homeworks/count");
  return data.count; // číslo, např. 3
}

async function getTimetable() {
  const data = await _proxyFetch("/dashboard/timetable");
  return data; // raw objekt z Bakalářů (Days, Subjects, Hours, ...)
}

async function getMarks() {
  const data = await _proxyFetch("/dashboard/marks");
  return data.marks; // pole: [{ subject, mark, caption, date, weight }, ...]
}

async function getUnreadMessages() {
  const data = await _proxyFetch("/dashboard/messages/unread");
  return { count: data.count, messages: data.messages };
}


// =============================================================================
// LOGOUT – pouze smaže lokální session (server je stateless)
// =============================================================================
function bakixLogout() {
  _session.accessToken = null;
  _session.schoolUrl   = null;
}


// =============================================================================
// UKÁZKA POUŽITÍ
// =============================================================================
async function main() {
  try {
    // 1. Přihlášení
    await bakixLogin(
      "https://skola.bakalari.cz", // URL školy
      "jan.novak",                  // uživatelské jméno
      "moje-heslo"                  // heslo (POZOR: HTTP = nešifrováno)
    );
    console.log("Přihlášení OK");

    // 2. Načtení dat
    const [homeworkCount, marks, { count: unreadCount }] = await Promise.all([
      getHomeworksCount(),
      getMarks(),
      getUnreadMessages(),
    ]);

    console.log("Nesplněné úkoly:", homeworkCount);
    console.log("Poslední známky:", marks.slice(0, 3));
    console.log("Nepřečtené zprávy:", unreadCount);

    // 3. Rozvrh zvlášť (větší payload)
    const timetable = await getTimetable();
    console.log("Rozvrh – počet dnů:", timetable.Days?.length);

  } catch (err) {
    if (err.status === 401) {
      console.error("Špatné přihlašovací údaje nebo expirovaný token.");
    } else {
      console.error("Chyba:", err.message, err.detail || "");
    }
  } finally {
    bakixLogout();
  }
}

// main(); // odkomentuj pro spuštění
