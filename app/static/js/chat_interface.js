'use strict';

document.addEventListener('DOMContentLoaded', function () {
  var fab          = document.getElementById('ai-fab');
  var sidebar      = document.getElementById('ai-sidebar');
  var overlay      = document.getElementById('ai-overlay');
  var thread       = document.getElementById('ai-thread');
  var input        = document.getElementById('ai-input');
  var sendBtn      = document.getElementById('ai-send');
  var suggestEl    = document.getElementById('ai-suggestions');
  var fabDot       = document.getElementById('ai-fab-dot');
  var modifyBtn    = document.getElementById('ai-modify-btn');
  var modifyPicker = document.getElementById('ai-modify-picker');
  var popoutBtn    = document.getElementById('ai-popout-btn');
  var convosBtn    = document.getElementById('ai-convos-btn');
  var newChatBtn   = document.getElementById('ai-newchat-btn');
  var sidebarHdr   = sidebar ? sidebar.querySelector('.ai-sidebar__header') : null;

  if (!fab || !sidebar) return;

  var chatHistory     = [];
  var initialRendered = false;
  var currentConversationId = localStorage.getItem('bakix_conversation_id') || null;
  var conversations   = [];
  var convosPanelEl   = null;
  var isPremium       = (typeof IS_PREMIUM !== 'undefined') ? !!IS_PREMIUM : false;
  var currentMode     = 'auto';
  var selectedModel   = localStorage.getItem('bakix_ai_model') || 'gemini-3.1-flash-lite';
  var selectedAiMode  = localStorage.getItem('bakix_ai_mode')  || 'normal';
  var userProjects    = (typeof USER_PROJECTS !== 'undefined' && Array.isArray(USER_PROJECTS))
                        ? USER_PROJECTS : [];
  var insightsPromise;
  var cmdPalette      = document.getElementById('ai-cmd-palette');
  var cmdActive       = -1;

  // ── Slash command palette ────────────────────────────────

  var SLASH_COMMANDS = [
    { cmd: '/studie plan',         desc: 'Personalizovaný studijní plán' },
    { cmd: '/shrnutí den',         desc: 'Shrnutí dnešních známek' },
    { cmd: '/shrnutí',             desc: 'Týdenní přehled učiva' },
    { cmd: '/skill create',        desc: 'Vytvoř nový AI skill' },
    { cmd: '/skill list',          desc: 'Zobraz uložené skilly' },
    { cmd: '/skill delete',        desc: 'Smaž skill' },
    { cmd: '/skill cancel',        desc: 'Zruš probíhající tvorbu' },
  ];

  function showCmdPalette(commands) {
    if (!cmdPalette || !commands.length) { hideCmdPalette(); return; }
    cmdPalette.innerHTML = '';
    cmdActive = -1;
    commands.forEach(function (item) {
      var el = document.createElement('div');
      el.className = 'ai-cmd-item';
      el.setAttribute('role', 'option');
      el.dataset.cmd = item.cmd;
      var cmdSpan = document.createElement('span');
      cmdSpan.className = 'ai-cmd-item__cmd';
      cmdSpan.textContent = item.cmd;
      var descSpan = document.createElement('span');
      descSpan.className = 'ai-cmd-item__desc';
      descSpan.textContent = item.desc;
      el.appendChild(cmdSpan);
      el.appendChild(descSpan);
      el.addEventListener('mousedown', function (e) {
        e.preventDefault();
        selectCmd(item.cmd);
      });
      cmdPalette.appendChild(el);
    });
    cmdPalette.style.display = '';
  }

  function hideCmdPalette() {
    if (!cmdPalette) return;
    cmdPalette.style.display = 'none';
    cmdPalette.innerHTML = '';
    cmdActive = -1;
  }

  function moveCmdSelection(dir) {
    var items = cmdPalette ? cmdPalette.querySelectorAll('.ai-cmd-item') : [];
    if (!items.length) return;
    if (cmdActive >= 0) items[cmdActive].classList.remove('ai-cmd-item--active');
    cmdActive = (cmdActive + dir + items.length) % items.length;
    items[cmdActive].classList.add('ai-cmd-item--active');
    items[cmdActive].scrollIntoView({ block: 'nearest' });
  }

  function selectCmd(cmd) {
    if (input) { input.value = cmd + ' '; input.focus(); }
    hideCmdPalette();
  }

  // ── Text-selection "Vysvětlit" button ────────────────────

  var _selBtn = document.createElement('button');
  _selBtn.textContent = '✦ Vysvětlit';
  _selBtn.style.cssText = (
    'display:none;position:fixed;z-index:9999;padding:4px 12px;font-size:12px;' +
    'border:none;border-radius:12px;background:var(--accent,#5c7a9e);color:#fff;' +
    'cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.25);white-space:nowrap;' +
    'font-family:inherit;line-height:1.4;'
  );
  document.body.appendChild(_selBtn);

  var _selText = '';

  function _hideSelBtn() {
    _selBtn.style.display = 'none';
    _selText = '';
  }

  document.addEventListener('mouseup', function (e) {
    if (_selBtn.contains(e.target)) return;
    var sel  = window.getSelection();
    var text = sel ? sel.toString().trim() : '';
    if (!text || text.length < 2 || text.length > 400) { _hideSelBtn(); return; }
    var range = sel.rangeCount ? sel.getRangeAt(0) : null;
    if (!range) { _hideSelBtn(); return; }
    var rect  = range.getBoundingClientRect();
    if (!rect.width && !rect.height) { _hideSelBtn(); return; }
    _selText = text;
    _selBtn.style.display = '';
    var btnW = _selBtn.offsetWidth || 96;
    var x    = rect.left + (rect.width - btnW) / 2;
    x        = Math.max(4, Math.min(window.innerWidth - btnW - 4, x));
    var y    = rect.top - 38;
    if (y < 6) y = rect.bottom + 6;
    _selBtn.style.left = Math.round(x) + 'px';
    _selBtn.style.top  = Math.round(y) + 'px';
  });

  document.addEventListener('mousedown', function (e) {
    if (!_selBtn.contains(e.target)) _hideSelBtn();
  });

  document.addEventListener('scroll', _hideSelBtn, true);

  _selBtn.addEventListener('click', function () {
    var text       = _selText;
    var wasOpen    = sidebar.classList.contains('open');
    _hideSelBtn();
    if (!text) return;
    if (!wasOpen) openSidebar();
    insightsPromise.then(function () {
      setTimeout(function () {
        sendMessage('Vysvětlit přes AI: ' + text);
      }, wasOpen ? 0 : 50);
    });
  });

  // ── Utility ──────────────────────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function parseMarkdown(escaped) {
    return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  }
  function fmtTime(iso) {
    if (!iso) return '';
    var d = new Date(iso);
    return isNaN(d) ? '' : d.toLocaleTimeString('cs-CZ', { hour: '2-digit', minute: '2-digit' });
  }
  function isWindowed() { return sidebar.classList.contains('ai-sidebar--windowed'); }

  /* Na mobilu input nefokusovat — vyskočila by klávesnice hned
     po kliknutí na ikonku a zakryla celý chat. */
  var FINE_POINTER = window.matchMedia('(hover: hover) and (pointer: fine)').matches;

  // ── Body scroll lock ──────────────────────────────────────
  /* overflow:hidden na body se nepropaguje (html má overflow-x:hidden)
     a na iOS nefunguje — position:fixed technika zamkne spolehlivě. */
  var scrollLocked = false, scrollLockY = 0;
  function lockScroll() {
    if (scrollLocked) return;
    scrollLocked = true;
    scrollLockY = window.scrollY || document.documentElement.scrollTop || 0;
    var s = document.body.style;
    s.position = 'fixed';
    s.top      = -scrollLockY + 'px';
    s.left     = '0';
    s.right    = '0';
    s.width    = '100%';
    s.overflow = 'hidden';
  }
  function unlockScroll() {
    if (!scrollLocked) return;
    scrollLocked = false;
    var s = document.body.style;
    s.position = ''; s.top = ''; s.left = ''; s.right = ''; s.width = ''; s.overflow = '';
    var de = document.documentElement;
    var prev = de.style.scrollBehavior;
    de.style.scrollBehavior = 'auto'; /* base.css má smooth — návrat musí být okamžitý */
    window.scrollTo(0, scrollLockY);
    de.style.scrollBehavior = prev;
  }

  // ── Sidebar open / close ──────────────────────────────────

  function openSidebar() {
    sidebar.classList.add('open');
    if (!isWindowed()) {
      overlay.classList.add('open');
      lockScroll();
    }
    if (input && FINE_POINTER) input.focus();

    if (!initialRendered) {
      initialRendered = true;
      renderInitial();
    }
  }

  // Show the active conversation's messages, or insights for a fresh chat.
  function renderInitial() {
    if (currentConversationId) {
      loadConversation(currentConversationId);
      return;
    }
    var placeholder = appendMsg('model', 'Analyzuji tvá studijní data…', false, null);
    placeholder.classList.add('ai-msg--thinking');
    insightsPromise.then(function (d) {
      placeholder.remove();
      if (d) renderInsights(d);
      else appendMsg('model', 'Nemám teď přístup ke tvým datům. Zeptej se mě na cokoliv!', false, null);
    });
  }

  // ── Conversations (multiple chats) ────────────────────────

  function loadConversation(id) {
    currentConversationId = id;
    localStorage.setItem('bakix_conversation_id', id);
    initialRendered = true;
    thread.innerHTML = '';
    chatHistory = [];
    suggestEl.innerHTML = '';
    convosPanelEl = null;
    fetch('/api/ai/conversations/' + id + '/messages')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var msgs = (d && d.messages) || [];
        if (!msgs.length) {
          appendMsg('model', 'Nový chat — zeptej se mě na cokoliv ✦', false, null);
          return;
        }
        msgs.forEach(function (m) {
          appendMsg(m.role, m.message, m.is_html, m.timestamp);
          chatHistory.push({ role: m.role === 'user' ? 'user' : 'model', text: m.message });
        });
      })
      .catch(function () {
        appendMsg('model', 'Nepodařilo se načíst tento chat.', false, null);
      });
  }

  function newChat() {
    currentConversationId = null;
    localStorage.removeItem('bakix_conversation_id');
    chatHistory = [];
    thread.innerHTML = '';
    suggestEl.innerHTML = '';
    convosPanelEl = null;
    initialRendered = true;
    var placeholder = appendMsg('model', 'Analyzuji tvá studijní data…', false, null);
    placeholder.classList.add('ai-msg--thinking');
    insightsPromise.then(function (d) {
      placeholder.remove();
      if (d) renderInsights(d);
      else appendMsg('model', 'Nový chat — zeptej se mě na cokoliv ✦', false, null);
    });
    if (input && FINE_POINTER) input.focus();
  }

  function refreshConversations() {
    return fetch('/api/ai/conversations')
      .then(function (r) { return r.json(); })
      .then(function (list) { if (Array.isArray(list)) conversations = list; return conversations; })
      .catch(function () { return conversations; });
  }

  function buildConvosPanel(list) {
    var panel = document.createElement('div');
    panel.className = 'ai-msg ai-msg--projects';

    var label = document.createElement('div');
    label.className = 'ai-section__label';
    label.textContent = 'Historie chatů';
    panel.appendChild(label);

    var listEl = document.createElement('div');
    listEl.className = 'projects-list-thread';

    var newRow = document.createElement('button');
    newRow.className = 'ai-suggestion-btn';
    newRow.textContent = '＋ Nový chat';
    newRow.addEventListener('click', function () {
      if (convosPanelEl) { convosPanelEl.remove(); convosPanelEl = null; }
      newChat();
    });
    listEl.appendChild(newRow);

    if (!list.length) {
      var empty = document.createElement('p');
      empty.className = 'projects-thread-empty';
      empty.textContent = 'Zatím žádné uložené chaty.';
      listEl.appendChild(empty);
    } else {
      list.forEach(function (cv) {
        var row = document.createElement('div');
        row.className = 'project-thread-row';

        var link = document.createElement('a');
        link.className = 'project-thread-link';
        link.href = '#';
        link.textContent = cv.title || 'Chat';
        link.title = cv.title || 'Chat';
        if (cv.id === currentConversationId) link.style.fontWeight = '700';
        link.addEventListener('click', function (e) {
          e.preventDefault();
          loadConversation(cv.id);
        });

        var delBtn = document.createElement('button');
        delBtn.className = 'project-thread-modify';
        delBtn.textContent = '×';
        delBtn.title = 'Smazat chat';
        delBtn.addEventListener('click', function (e) {
          e.stopPropagation();
          deleteConversation(cv.id);
        });

        row.appendChild(link);
        row.appendChild(delBtn);
        listEl.appendChild(row);
      });
    }

    panel.appendChild(listEl);
    return panel;
  }

  function showConversationsPanel() {
    if (convosPanelEl && thread.contains(convosPanelEl)) {
      convosPanelEl.remove();
      convosPanelEl = null;
      return;
    }
    convosPanelEl = buildConvosPanel(conversations);
    thread.appendChild(convosPanelEl);
    thread.scrollTop = thread.scrollHeight;
    refreshConversations().then(function (list) {
      if (convosPanelEl && thread.contains(convosPanelEl)) {
        var updated = buildConvosPanel(list);
        thread.replaceChild(updated, convosPanelEl);
        convosPanelEl = updated;
        thread.scrollTop = thread.scrollHeight;
      }
    });
  }

  function deleteConversation(id) {
    fetch('/api/ai/conversations/' + id, { method: 'DELETE' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        conversations = conversations.filter(function (c) { return c.id !== id; });
        if (id === currentConversationId) { newChat(); return; }
        if (convosPanelEl && thread.contains(convosPanelEl)) {
          var updated = buildConvosPanel(conversations);
          thread.replaceChild(updated, convosPanelEl);
          convosPanelEl = updated;
        }
      })
      .catch(function () {});
  }

  if (convosBtn)  convosBtn.addEventListener('click', function (e) { e.stopPropagation(); showConversationsPanel(); });
  if (newChatBtn) newChatBtn.addEventListener('click', function () { newChat(); });

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
    unlockScroll();
  }

  fab.addEventListener('click', openSidebar);
  overlay.addEventListener('click', function () { if (!isWindowed()) closeSidebar(); });
  document.getElementById('ai-sidebar-close')?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar.classList.contains('open')) closeSidebar();
  });

  // ── Pop-out / window mode ─────────────────────────────────

  function enterWindowMode() {
    // Centre the window initially
    var sw = Math.min(420, window.innerWidth - 16);
    var sh = Math.min(600, window.innerHeight - 32);
    var cx = Math.round((window.innerWidth  - sw) / 2);
    var cy = Math.round((window.innerHeight - sh) / 2);
    sidebar.style.setProperty('--chat-x', cx + 'px');
    sidebar.style.setProperty('--chat-y', cy + 'px');
    sidebar.style.width = sw + 'px';

    sidebar.classList.add('ai-sidebar--windowed');
    overlay.classList.remove('open');
    unlockScroll(); /* okenní režim nechává stránku scrollovat */

    if (popoutBtn) { popoutBtn.textContent = '⊟'; popoutBtn.title = 'Zpět do postranního panelu'; }
    if (!sidebar.classList.contains('open')) openSidebar();
  }

  function exitWindowMode() {
    sidebar.classList.remove('ai-sidebar--windowed', 'dragging');
    sidebar.style.removeProperty('--chat-x');
    sidebar.style.removeProperty('--chat-y');
    sidebar.style.removeProperty('width');

    if (sidebar.classList.contains('open')) {
      overlay.classList.add('open');
      lockScroll();
    }
    if (popoutBtn) { popoutBtn.textContent = '⊞'; popoutBtn.title = 'Okenní režim'; }
  }

  if (popoutBtn) {
    popoutBtn.addEventListener('click', function () {
      isWindowed() ? exitWindowMode() : enterWindowMode();
    });
  }

  // ── Drag (window mode only) ───────────────────────────────

  var isDragging = false, dragOX = 0, dragOY = 0;

  if (sidebarHdr) {
    sidebarHdr.addEventListener('mousedown', function (e) {
      if (!isWindowed() || e.button !== 0) return;
      isDragging = true;
      var rect = sidebar.getBoundingClientRect();
      dragOX = e.clientX - rect.left;
      dragOY = e.clientY - rect.top;
      sidebar.classList.add('dragging');
      e.preventDefault();
    });
  }

  document.addEventListener('mousemove', function (e) {
    if (!isDragging) return;
    var x = Math.max(0, Math.min(window.innerWidth  - sidebar.offsetWidth,  e.clientX - dragOX));
    var y = Math.max(0, Math.min(window.innerHeight - sidebar.offsetHeight, e.clientY - dragOY));
    sidebar.style.setProperty('--chat-x', x + 'px');
    sidebar.style.setProperty('--chat-y', y + 'px');
  });

  document.addEventListener('mouseup', function () {
    if (isDragging) { isDragging = false; sidebar.classList.remove('dragging'); }
  });

  // ── Chat mode toggle ──────────────────────────────────────

  document.querySelectorAll('.chat-mode-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.chat-mode-btn').forEach(function (b) {
        b.classList.remove('chat-mode-btn--active');
      });
      btn.classList.add('chat-mode-btn--active');
      currentMode = btn.dataset.mode;
    });
  });

  // ── Model selector ───────────────────────────────────────

  var modelBtn   = document.getElementById('ai-model-btn');
  var modelPanel = document.getElementById('ai-model-panel');
  var modelName  = document.getElementById('ai-model-name');
  var modelBadge = document.getElementById('ai-model-badge');

  function applyModel(modelId, name, group) {
    selectedModel = modelId;
    localStorage.setItem('bakix_ai_model', modelId);
    if (modelName) modelName.textContent = name;
    if (modelBadge) {
      modelBadge.textContent = group === 'freemium' ? 'Free' : 'Pro';
      modelBadge.className   = 'ai-model-badge' + (group === 'freemium' ? ' ai-model-badge--freemium' : '');
    }
    if (modelPanel) {
      modelPanel.querySelectorAll('.ai-model-opt').forEach(function (o) {
        var active = o.dataset.model === modelId;
        o.classList.toggle('active', active);
        o.setAttribute('aria-selected', active ? 'true' : 'false');
      });
    }
  }

  function _modelOpt(id) {
    if (!modelPanel || !id) return null;
    try { return modelPanel.querySelector('[data-model="' + (window.CSS && CSS.escape ? CSS.escape(id) : id) + '"]'); }
    catch (e) { return null; }
  }

  // Restore saved model on load. Free users can't keep a Pro model — fall back
  // to the first freemium option, and flag Pro options as locked (🔒).
  (function () {
    if (!modelPanel) return;
    if (!isPremium) {
      var cur = _modelOpt(selectedModel);
      if (!cur || cur.dataset.group === 'pro') {
        var ff = modelPanel.querySelector('.ai-model-opt[data-group="freemium"]');
        if (ff) selectedModel = ff.dataset.model;
      }
      modelPanel.querySelectorAll('.ai-model-opt[data-group="pro"]').forEach(function (o) {
        o.classList.add('ai-model-opt--locked');
        var desc = o.querySelector('.ai-model-opt__desc');
        if (desc && desc.textContent.indexOf('🔒') === -1) desc.textContent = '🔒 Premium';
      });
    }
    var saved = _modelOpt(selectedModel);
    if (saved) applyModel(selectedModel, saved.dataset.name, saved.dataset.group);
  })();

  if (modelBtn && modelPanel) {
    modelBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      modelPanel.classList.toggle('open');
    });
    modelPanel.querySelectorAll('.ai-model-opt').forEach(function (opt) {
      opt.addEventListener('mousedown', function (e) {
        e.preventDefault();
        if (!isPremium && opt.dataset.group === 'pro') {
          modelPanel.classList.remove('open');
          showUpgradeHint('Tento model je součástí Premium.');
          return;
        }
        applyModel(opt.dataset.model, opt.dataset.name, opt.dataset.group);
        modelPanel.classList.remove('open');
      });
    });
    document.addEventListener('click', function (e) {
      if (!modelPanel.contains(e.target) && e.target !== modelBtn) {
        modelPanel.classList.remove('open');
      }
    });
  }

  // ── AI mode toggle (Normal / Přemýšlení) ─────────────────

  var _THINKING_STEPS = [
    '💭 Přemýšlím…',
    '🔍 Analyzuji kontext…',
    '✏️ Formuluji odpověď…',
    '🧠 Ještě chvíli…',
  ];
  var _thinkingTimer = null;

  function applyAiMode(mode) {
    selectedAiMode = mode;
    localStorage.setItem('bakix_ai_mode', mode);
    document.querySelectorAll('.ai-mode-btn').forEach(function (b) {
      b.classList.toggle('active', b.dataset.aimode === mode);
    });
  }

  // Thinking mode is Premium-only — force Normal and lock the button for free.
  if (!isPremium && selectedAiMode === 'thinking') selectedAiMode = 'normal';
  if (!isPremium) {
    document.querySelectorAll('.ai-mode-btn[data-aimode="thinking"]').forEach(function (b) {
      b.classList.add('ai-mode-btn--locked');
      if (b.textContent.indexOf('🔒') === -1) b.textContent = '🔒 ' + b.textContent;
    });
  }

  // Restore saved mode on load
  applyAiMode(selectedAiMode);

  document.querySelectorAll('.ai-mode-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (btn.dataset.aimode === 'thinking' && !isPremium) {
        showUpgradeHint('Režim Přemýšlení je součástí Premium.');
        return;
      }
      applyAiMode(btn.dataset.aimode);
    });
  });

  function startThinkingAnim(el) {
    if (selectedAiMode !== 'thinking') return;
    var i = 0;
    el.classList.add('ai-msg--thinking-mode');
    el.innerHTML = '<span class="ai-thinking-dot">💭</span> <span class="ai-thinking-dot">·</span><span class="ai-thinking-dot">·</span><span class="ai-thinking-dot">·</span>';
    _thinkingTimer = setInterval(function () {
      i = (i + 1) % _THINKING_STEPS.length;
      el.textContent = _THINKING_STEPS[i];
    }, 1800);
  }

  function stopThinkingAnim(el) {
    if (_thinkingTimer) { clearInterval(_thinkingTimer); _thinkingTimer = null; }
    el.classList.remove('ai-msg--thinking-mode');
  }

  // ── Message builder ───────────────────────────────────────

  function appendMsg(role, content, isHtml, timestamp) {
    var el = document.createElement('div');
    el.className = 'ai-msg ai-msg--' + role;
    el.innerHTML = isHtml ? content : parseMarkdown(esc(content));

    if (timestamp) {
      var ts = document.createElement('div');
      ts.className = 'chat-msg-ts';
      ts.textContent = fmtTime(timestamp);
      el.appendChild(ts);
    }

    thread.appendChild(el);
    renderMath(el);
    thread.scrollTop = thread.scrollHeight;
    return el;
  }

  function showSuggestions(items) {
    suggestEl.innerHTML = '';
    items.forEach(function (text) {
      var btn = document.createElement('button');
      btn.className = 'ai-suggestion-btn';
      btn.textContent = text;
      btn.addEventListener('click', function () { suggestEl.innerHTML = ''; sendMessage(text); });
      suggestEl.appendChild(btn);
    });
  }

  // ── LaTeX rendering (KaTeX) for AI messages ───────────────

  function renderMath(el) {
    if (!el || !window.renderMathInElement) return;
    try {
      renderMathInElement(el, {
        delimiters: [
          { left: '$$', right: '$$', display: true  },
          { left: '$',  right: '$',  display: false },
          { left: '\\[', right: '\\]', display: true  },
          { left: '\\(', right: '\\)', display: false },
        ],
        throwOnError: false,
      });
    } catch (e) { /* malformed LaTeX → leave as-is */ }
  }

  // ── Premium upgrade hint (reuses rate-limit CTA styling) ──

  function showUpgradeHint(text) {
    var cta = document.createElement('div');
    cta.className = 'ai-rate-cta';
    cta.innerHTML =
      '<span class="ai-rate-cta__text">' + esc(text) + '</span>' +
      '<button class="ai-rate-cta__btn" onclick="document.getElementById(\'settings-open\') && document.getElementById(\'settings-open\').click()">✦ Premium</button>';
    thread.appendChild(cta);
    thread.scrollTop = thread.scrollHeight;
  }

  // ── Render initial AI insights ────────────────────────────

  function renderInsights(d) {
    var parts = [];
    if (d.alert)          parts.push('<strong>' + esc(d.alert) + '</strong>');
    if (d.recommendation) parts.push(esc(d.recommendation));
    var html = parts.join('<br><br>');

    if (d.exercise) {
      html += '<div class="ai-exercise-block" id="ai-exercise-block">'
            + '<div class="ai-section__label">Cvičení</div>'
            + '<div class="ai-exercise-q">' + esc(d.exercise) + '</div>'
            + '<div class="ai-exercise-form">'
            + '<input class="ai-chat__input" type="text" id="ai-exercise-input"'
            + '  placeholder="Napiš svou odpověď…" autocomplete="off">'
            + '<button class="btn btn--secondary" id="ai-exercise-submit">Zkontrolovat</button>'
            + '</div></div>';
    }

    chatHistory.push({ role: 'model', text: [d.alert, d.recommendation, d.exercise ? 'Cvičení: ' + d.exercise : null].filter(Boolean).join('\n\n') });
    appendMsg('model', html, true, null);

    if (d.exercise) {
      var exInput  = document.getElementById('ai-exercise-input');
      var exSubmit = document.getElementById('ai-exercise-submit');
      var exBlock  = document.getElementById('ai-exercise-block');
      function submitExercise() {
        var answer = exInput.value.trim();
        if (!answer) return;
        if (exBlock) exBlock.style.display = 'none';
        sendMessage('Zkontroluj moji odpověď:\nOtázka: ' + d.exercise + '\nMoje odpověď: ' + answer);
      }
      if (exSubmit) exSubmit.addEventListener('click', submitExercise);
      if (exInput)  exInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') submitExercise(); });
    }

    var suggestions = [];
    if (d.chat_prompt) suggestions.push(d.chat_prompt);
    suggestions.push('Vytvoř mi procvičovací test');
    suggestions.push('Jak se zlepšit?');
    showSuggestions(suggestions.slice(0, 3));
  }

  // ── Send message ──────────────────────────────────────────

  function sendMessage(text) {
    if (!text) return;
    hideCmdPalette();
    appendMsg('user', text, false, new Date().toISOString());
    chatHistory.push({ role: 'user', text: text });
    if (input) { input.value = ''; delete input.dataset.modifyPageId; }
    if (sendBtn) sendBtn.disabled = true;
    suggestEl.innerHTML = '';

    var thinking = appendMsg('model', '…', false, null);
    thinking.classList.add('ai-msg--thinking');
    startThinkingAnim(thinking);

    fetch('/api/ai/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, history: chatHistory.slice(0, -1), chat_mode: currentMode, model_id: selectedModel, ai_mode: selectedAiMode, conversation_id: currentConversationId }),
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        stopThinkingAnim(thinking);
        // Adopt the chat id the server used (a brand-new chat gets one created).
        if (r.conversation_id) {
          currentConversationId = r.conversation_id;
          localStorage.setItem('bakix_conversation_id', r.conversation_id);
          refreshConversations();
        }
        var reply = r.message || r.error || 'Nastala chyba.';
        thinking.innerHTML = r.is_html ? reply : parseMarkdown(esc(reply));
        renderMath(thinking);
        thinking.classList.remove('ai-msg--thinking');
        if (r.is_html)  thinking.classList.add('ai-msg--rich');
        if (r.is_test)  thinking.classList.add('ai-msg--test');

        if (r.timestamp) {
          var ts = document.createElement('div');
          ts.className = 'chat-msg-ts';
          ts.textContent = fmtTime(r.timestamp);
          thinking.appendChild(ts);
        }

        chatHistory.push({ role: 'model', text: reply });

        if (r.rate_limited) {
          var cta = document.createElement('div');
          cta.className = 'ai-rate-cta';
          if (r.tier === 'premium') {
            cta.innerHTML = '<span class="ai-rate-cta__text">Denní limit Premiumu je vyčerpán — vrátí se za 24 h.</span>';
          } else {
            cta.innerHTML =
              '<span class="ai-rate-cta__text">Bezplatný limit (5 dotazů/den) vyčerpán.</span>' +
              '<button class="ai-rate-cta__btn" onclick="document.getElementById(\'settings-open\').click()">' +
              '✦ Upgradovat na Premium</button>';
          }
          thread.appendChild(cta);
          thread.scrollTop = thread.scrollHeight;
        }

        if (r.action_url && r.action_label) {
          var btn = document.createElement('a');
          btn.href = r.action_url; btn.target = '_blank'; btn.rel = 'noopener noreferrer';
          btn.className = 'ai-action-btn'; btn.textContent = r.action_label + ' ↗';
          thread.appendChild(btn);
          thread.scrollTop = thread.scrollHeight;
          refreshProjects();
        }
      })
      .catch(function () {
        stopThinkingAnim(thinking);
        thinking.textContent = 'Síťová chyba. Zkus to znovu.';
        thinking.classList.remove('ai-msg--thinking');
      })
      .finally(function () { if (sendBtn) sendBtn.disabled = false; });
  }

  if (sendBtn) sendBtn.addEventListener('click', function () {
    hideCmdPalette();
    sendMessage((input ? input.value : '').trim());
  });

  if (input) input.addEventListener('input', function () {
    var val = input.value;
    if (!val.startsWith('/')) { hideCmdPalette(); return; }
    var query = val.slice(1).toLowerCase();
    var filtered = SLASH_COMMANDS.filter(function (c) {
      return c.cmd.slice(1).toLowerCase().startsWith(query);
    });
    showCmdPalette(filtered);
  });

  // ── Knowix tip banner (link to the bigger chat + command hints) ────
  var knowixTip = document.getElementById('ai-knowix-tip');
  if (knowixTip) {
    if (localStorage.getItem('bakix_knowix_tip_dismissed')) {
      knowixTip.style.display = 'none';
    }
    var knowixTipClose = document.getElementById('ai-knowix-tip-close');
    if (knowixTipClose) knowixTipClose.addEventListener('click', function () {
      knowixTip.style.display = 'none';
      localStorage.setItem('bakix_knowix_tip_dismissed', '1');
    });
    knowixTip.querySelectorAll('.ai-tip-cmd').forEach(function (btn) {
      btn.addEventListener('click', function () {
        if (!input) return;
        input.value = btn.dataset.cmd;
        input.focus();
        // Re-use the input listener so the command palette opens right away.
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    });
  }

  if (input) input.addEventListener('keydown', function (e) {
    var paletteVisible = cmdPalette && cmdPalette.style.display !== 'none';
    if (paletteVisible) {
      if (e.key === 'ArrowUp')   { e.preventDefault(); moveCmdSelection(-1); return; }
      if (e.key === 'ArrowDown') { e.preventDefault(); moveCmdSelection(1);  return; }
      if (e.key === 'Escape')    { e.preventDefault(); hideCmdPalette();     return; }
      if (e.key === 'Tab' && !e.shiftKey) {
        e.preventDefault();
        var items = cmdPalette.querySelectorAll('.ai-cmd-item');
        selectCmd((cmdActive >= 0 && items[cmdActive] ? items[cmdActive] : items[0]).dataset.cmd);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        var items = cmdPalette.querySelectorAll('.ai-cmd-item');
        if (cmdActive >= 0 && items[cmdActive]) {
          e.preventDefault();
          selectCmd(items[cmdActive].dataset.cmd);
          return;
        }
        hideCmdPalette();
        // fall through to send
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input.value.trim()); }
  });

  // ── Star (✦) button — show projects in chat thread ───────

  var projectsPanelEl = null; // track the current in-thread panel

  function showProjectsInThread() {
    // Toggle: if panel already shown at end of thread, remove it
    if (projectsPanelEl && thread.contains(projectsPanelEl)) {
      projectsPanelEl.remove();
      projectsPanelEl = null;
      return;
    }

    // Build the panel immediately from cached data, then refresh
    projectsPanelEl = buildProjectsPanel(userProjects);
    thread.appendChild(projectsPanelEl);
    thread.scrollTop = thread.scrollHeight;

    // Refresh from server
    fetch('/api/ai/pages')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!Array.isArray(data)) return;
        userProjects = data;
        if (projectsPanelEl && thread.contains(projectsPanelEl)) {
          var updated = buildProjectsPanel(userProjects);
          thread.replaceChild(updated, projectsPanelEl);
          projectsPanelEl = updated;
          thread.scrollTop = thread.scrollHeight;
        }
        renderProjectsCard(userProjects);
      })
      .catch(function () {});
  }

  function buildProjectsPanel(projects) {
    var panel = document.createElement('div');
    panel.className = 'ai-msg ai-msg--projects';

    var label = document.createElement('div');
    label.className = 'ai-section__label';
    label.textContent = 'Moje projekty';
    panel.appendChild(label);

    var list = document.createElement('div');
    list.className = 'projects-list-thread';

    if (!projects.length) {
      var empty = document.createElement('p');
      empty.className = 'projects-thread-empty';
      empty.textContent = 'Zatím žádné projekty. Popros AI o vytvoření stránky!';
      list.appendChild(empty);
    } else {
      projects.forEach(function (p) {
        var row = document.createElement('div');
        row.className = 'project-thread-row';

        var link = document.createElement('a');
        link.className = 'project-thread-link';
        link.href = '/api/ai/generated/' + p.page_id;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = p.topic;
        link.title = p.topic;

        var modBtn = document.createElement('button');
        modBtn.className = 'project-thread-modify';
        modBtn.textContent = 'Upravit';
        modBtn.addEventListener('click', function () {
          if (input) {
            input.value = 'Uprav projekt "' + p.topic + '"';
            input.dataset.modifyPageId = p.page_id;
            input.focus();
          }
          if (projectsPanelEl) { projectsPanelEl.remove(); projectsPanelEl = null; }
        });

        var delBtn = document.createElement('button');
        delBtn.className = 'project-thread-modify';
        delBtn.textContent = '×';
        delBtn.title = 'Smazat projekt';
        delBtn.addEventListener('click', function () { deleteProject(p.page_id); });

        row.appendChild(link);
        row.appendChild(modBtn);
        row.appendChild(delBtn);
        list.appendChild(row);
      });
    }

    panel.appendChild(list);
    return panel;
  }

  if (modifyBtn) {
    modifyBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      showProjectsInThread();
    });
  }

  // Keep the dropdown picker hidden (still used internally if needed)
  if (modifyPicker) modifyPicker.style.display = 'none';

  // ── Projects card sync ────────────────────────────────────

  function deleteProject(pageId) {
    fetch('/api/ai/generated/' + pageId, { method: 'DELETE' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) return;
        userProjects = userProjects.filter(function (p) { return p.page_id !== pageId; });
        renderProjectsCard(userProjects);
        if (projectsPanelEl && thread.contains(projectsPanelEl)) {
          var updated = buildProjectsPanel(userProjects);
          thread.replaceChild(updated, projectsPanelEl);
          projectsPanelEl = updated;
        }
      })
      .catch(function () {});
  }

  function renderProjectsCard(projects) {
    var body = document.getElementById('my-projects-body');
    if (!body) return;
    if (!projects.length) {
      body.innerHTML = '<p class="empty">Zatím žádné projekty. Popros AI o vytvoření stránky!</p>';
      return;
    }
    var grid = document.createElement('div');
    grid.className = 'projects-grid';
    projects.forEach(function (p) {
      var a = document.createElement('a');
      a.className = 'project-link';
      a.href = '/api/ai/generated/' + p.page_id;
      var span = document.createElement('span');
      span.className = 'project-link__title';
      span.textContent = p.topic;
      a.appendChild(span);
      var del = document.createElement('button');
      del.className = 'project-link__del';
      del.textContent = '×';
      del.title = 'Smazat projekt';
      del.setAttribute('aria-label', 'Smazat projekt');
      del.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        deleteProject(p.page_id);
      });
      a.appendChild(del);
      grid.appendChild(a);
    });
    body.innerHTML = '';
    body.appendChild(grid);
  }

  function refreshProjects() {
    fetch('/api/ai/pages')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (Array.isArray(data)) { userProjects = data; renderProjectsCard(userProjects); }
      })
      .catch(function () {});
  }

  // Replace server-rendered project grid with JS version (adds delete buttons)
  renderProjectsCard(userProjects);

  // ── Proactive summary card ────────────────────────────────

  var summaryCard    = document.getElementById('ai-summary-card');
  var summaryText    = document.getElementById('ai-summary-text');
  var summaryDismiss = document.getElementById('ai-summary-dismiss');

  if (summaryDismiss) {
    summaryDismiss.addEventListener('click', function () {
      if (summaryCard) summaryCard.style.display = 'none';
    });
  }

  window.openAiWithMessage = function (greeting) {
    openSidebar();
    insightsPromise.then(function () { setTimeout(function () { sendMessage(greeting); }, 50); });
  };

  // ── Fetch insights on page load ───────────────────────────

  insightsPromise = fetch('/api/gemini/insights')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.error && (d.alert || d.recommendation || d.exercise)) {
        if (fabDot) fabDot.style.display = '';
        var summary = d.alert || d.recommendation || '';
        if (summary && summaryCard && summaryText) {
          summaryText.innerHTML = parseMarkdown(esc(summary));
          summaryCard.style.display = '';
        }
        return d;
      }
      return null;
    })
    .catch(function () { return null; });
});
