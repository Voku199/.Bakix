'use strict';

document.addEventListener('DOMContentLoaded', function () {
  var fab           = document.getElementById('ai-fab');
  var sidebar       = document.getElementById('ai-sidebar');
  var overlay       = document.getElementById('ai-overlay');
  var thread        = document.getElementById('ai-thread');
  var input         = document.getElementById('ai-input');
  var sendBtn       = document.getElementById('ai-send');
  var suggestionsEl = document.getElementById('ai-suggestions');
  var fabDot        = document.getElementById('ai-fab-dot');

  if (!fab || !sidebar) return;

  var chatHistory     = [];
  var initialRendered = false;
  var insightsPromise; // assigned below after all functions are defined

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function parseMarkdown(escaped) {
    // Converts **text** → <strong>text</strong> after HTML-escaping
    return escaped.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  }

  // ── Sidebar open / close ──────────────────────────────────
  function openSidebar() {
    sidebar.classList.add('open');
    overlay.classList.add('open');
    if (input) input.focus();

    if (!initialRendered) {
      initialRendered = true;
      var placeholder = appendMsg('model', 'Analyzuji tvá studijní data…', false);
      placeholder.classList.add('ai-msg--thinking');

      insightsPromise.then(function (d) {
        placeholder.remove();
        if (d) {
          renderInsights(d);
        } else {
          appendMsg('model', 'Nemám teď přístup ke tvým datům. Zeptej se mě na cokoliv!', false);
        }
      });
    }
  }

  function closeSidebar() {
    sidebar.classList.remove('open');
    overlay.classList.remove('open');
  }

  fab.addEventListener('click', openSidebar);
  overlay.addEventListener('click', closeSidebar);
  document.getElementById('ai-sidebar-close')?.addEventListener('click', closeSidebar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && sidebar.classList.contains('open')) closeSidebar();
  });

  // ── Message helpers ───────────────────────────────────────
  function appendMsg(role, content, isHtml) {
    var el = document.createElement('div');
    el.className = 'ai-msg ai-msg--' + role;
    if (isHtml) {
      el.innerHTML = content;
    } else {
      // Escape HTML first, then parse **bold** markers
      el.innerHTML = parseMarkdown(esc(content));
    }
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;
    return el;
  }

  function showSuggestions(items) {
    suggestionsEl.innerHTML = '';
    items.forEach(function (text) {
      var btn = document.createElement('button');
      btn.className = 'ai-suggestion-btn';
      btn.textContent = text;
      btn.addEventListener('click', function () {
        suggestionsEl.innerHTML = '';
        sendMessage(text);
      });
      suggestionsEl.appendChild(btn);
    });
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

    var modelText = [
      d.alert,
      d.recommendation,
      d.exercise ? 'Cvičení: ' + d.exercise : null,
    ].filter(Boolean).join('\n\n');
    chatHistory.push({ role: 'model', text: modelText });

    appendMsg('model', html, true);

    // Wire exercise form
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
      if (exInput)  exInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') submitExercise();
      });
    }

    // Suggested actions
    var suggestions = [];
    if (d.chat_prompt) suggestions.push(d.chat_prompt);
    suggestions.push('Vytvoř mi procvičovací test');
    suggestions.push('Jak se zlepšit?');
    showSuggestions(suggestions.slice(0, 3));
  }

  // ── Send a chat message ───────────────────────────────────
  function sendMessage(text) {
    if (!text) return;
    appendMsg('user', text, false);
    chatHistory.push({ role: 'user', text: text });
    if (input) input.value = '';
    if (sendBtn) sendBtn.disabled = true;
    suggestionsEl.innerHTML = '';

    var thinkingEl = appendMsg('model', '…', false);
    thinkingEl.classList.add('ai-msg--thinking');

    fetch('/api/ai/chat', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ message: text, history: chatHistory.slice(0, -1) }),
    })
      .then(function (r) { return r.json(); })
      .then(function (r) {
        var reply = r.message || r.error || 'Nastala chyba.';
        thinkingEl.innerHTML = parseMarkdown(esc(reply));
        thinkingEl.classList.remove('ai-msg--thinking');
        if (r.is_test) thinkingEl.classList.add('ai-msg--test');
        chatHistory.push({ role: 'model', text: reply });

        if (r.action_url && r.action_label) {
          var btn = document.createElement('a');
          btn.href      = r.action_url;
          btn.target    = '_blank';
          btn.rel       = 'noopener noreferrer';
          btn.className = 'ai-action-btn';
          btn.textContent = r.action_label + ' ↗';
          thread.appendChild(btn);
          thread.scrollTop = thread.scrollHeight;
        }
      })
      .catch(function () {
        thinkingEl.textContent = 'Síťová chyba. Zkus to znovu.';
        thinkingEl.classList.remove('ai-msg--thinking');
      })
      .finally(function () { if (sendBtn) sendBtn.disabled = false; });
  }

  if (sendBtn) sendBtn.addEventListener('click', function () {
    sendMessage((input ? input.value : '').trim());
  });
  if (input) input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(input.value.trim()); }
  });

  // ── Proactive summary card ────────────────────────────────
  var summaryCard    = document.getElementById('ai-summary-card');
  var summaryText    = document.getElementById('ai-summary-text');
  var summaryDismiss = document.getElementById('ai-summary-dismiss');

  if (summaryDismiss) {
    summaryDismiss.addEventListener('click', function () {
      if (summaryCard) summaryCard.style.display = 'none';
    });
  }

  // Opens sidebar and, after insights have rendered, sends a greeting message
  window.openAiWithMessage = function (greeting) {
    openSidebar();
    insightsPromise.then(function () {
      setTimeout(function () { sendMessage(greeting); }, 50);
    });
  };

  // ── Fetch insights on page load ───────────────────────────
  insightsPromise = fetch('/api/gemini/insights')
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.error && (d.alert || d.recommendation || d.exercise)) {
        if (fabDot) fabDot.style.display = '';

        // Populate the summary card with the alert (or recommendation as fallback)
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
