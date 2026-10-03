/* dashboard-chat.js — estado e transporte do Piggy; a ilha React possui o painel. */
(function () {
  const ui = window.PigBankChatUI;
  if (!ui) return;
  const messages = [];
  let draft = '';
  let busy = false;
  let loaded = false;
  let sequence = 0;
  let usage = null;
  let status = '';
  const fab = document.getElementById('piggy-fab');
  const suggestions = [
    'Quanto eu gastei esse mês?',
    'Quais minhas maiores categorias de gasto?',
    'Quanto tenho de saldo?',
    'Mostra meus últimos lançamentos',
  ];

  function view() {
    const pct = usage?.limit > 0 ? usage.used / usage.limit : 0;
    return {
      title: 'Piggy', subtitle: 'IA das suas finanças', avatar: '/brand/stickers/hello.webp',
      messages: messages.map(message => ({ ...message })), draft, disabled: busy, status,
      emptyText: 'Oi! Sou o Piggy. Posso ajudar você a entender suas finanças.',
      suggestions: suggestions.map(label => ({ label, onClick: () => window.piggyAsk(label) })),
      usage: pct >= 1 ? `Limite mensal atingido (${usage.used} / ${usage.limit}). Reseta no dia 1º.`
        : pct >= 0.8 ? `${usage.used} / ${usage.limit} mensagens este mês` : '',
      usageTone: pct >= 1 ? 'error' : pct >= 0.8 ? 'warning' : 'normal',
      onDraftChange: value => { draft = value; status = ''; render(); },
      onSend: () => window.piggySend(),
      onHidden: () => { fab?.classList.remove('open'); },
    };
  }
  function render() { ui.update('piggy', view()); }
  ui.register('piggy', view());

  window.togglePiggy = function () {
    if (!window.isProUser || !isProUser()) {
      if (window.showUpgradeModal) showUpgradeModal('ai_chat');
      return;
    }
    if (ui.isOpen('piggy')) {
      window.closePiggy();
    } else {
      ui.open('piggy');
      fab?.classList.add('open');
      if (!loaded) loadUsage();
      ui.focusInput('piggy');
    }
  };
  window.closePiggy = function () { ui.close('piggy'); };
  window.piggyAsk = function (text) {
    if (busy) return;
    draft = text;
    window.piggySend();
  };

  async function loadUsage() {
    // A tela inicia vazia a cada carregamento; o contexto persistido do Piggy
    // continua sendo responsabilidade do backend. Não exibir histórico de outros canais.
    loaded = true;
    try {
      const response = await fetch('/ai/messages?limit=1', { credentials: 'same-origin' });
      if (!response.ok) return;
      const data = await response.json();
      // Uma leitura iniciada ao abrir não deve sobrescrever a cota de um envio posterior.
      if (!messages.length) { usage = data.usage || usage; render(); }
    } catch (error) {
      console.warn('[piggy] erro ao buscar usage:', error);
    }
  }

  window.piggySend = async function () {
    if (busy) return;
    const text = draft.trim();
    if (!text) return;
    if (text.length > 2000) {
      status = 'Mensagem muito longa (máx 2000 caracteres).';
      render();
      return;
    }
    busy = true;
    status = '';
    draft = '';
    const createdAt = new Date().toISOString();
    messages.push({ id: `piggy-${++sequence}`, role: 'user', content: text, createdAt });
    const reply = { id: `piggy-${++sequence}`, role: 'assistant', content: 'Preparando a resposta…', state: 'pending', markdown: true };
    messages.push(reply);
    render();
    try {
      const response = await fetch('/ai/chat', {
        method: 'POST', credentials: 'same-origin',
        headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ message: text }),
      });
      if (response.status === 403) {
        Object.assign(reply, { state: 'error', content: 'Conversar com a IA é um recurso do PigBank+. [Faça upgrade](/precos) pra liberar.' });
        return;
      }
      if (!response.ok) {
        Object.assign(reply, { state: 'error', content: response.status === 429
          ? 'Muitas mensagens em pouco tempo. Aguarde um instante antes de enviar outra pergunta.'
          : 'Não foi possível obter a resposta. Se você pediu uma alteração, confira seus dados antes de repetir o pedido.' });
        return;
      }
      const data = await response.json();
      if (typeof data.reply !== 'string' || !data.reply.trim()) {
        Object.assign(reply, { state: 'error', content: 'A resposta não chegou completa. Se você pediu uma alteração, confira seus dados antes de repetir o pedido.' });
        return;
      }
      Object.assign(reply, { state: 'complete', content: data.reply });
      usage = data.usage || usage;
    } catch (error) {
      console.error('[piggy] erro no send:', error);
      // O Piggy executa escritas: falha de rede não prova que a ação falhou.
      // Não reenviar automaticamente, oferecer replay ou afirmar que não houve cobrança.
      Object.assign(reply, { state: 'error', content: 'A conexão foi interrompida antes de recebermos a resposta. Se você pediu uma alteração, confira seus dados antes de repetir o pedido.' });
    } finally {
      reply.createdAt = new Date().toISOString();
      busy = false;
      render();
      if (ui.isOpen('piggy')) ui.focusInput('piggy');
    }
  };

  // ── Bolinha: clique abre o chat + arrasto (só no app/PWA) ───────────────
  (function initFab() {
    const fab = document.getElementById("piggy-fab");
    if (!fab) return;

    let justDragged = false;

    // Toque/click abre o chat — em QUALQUER contexto (site e app). Também cobre
    // teclado (Enter/Espaço no botão dispara click). Substitui o onclick inline.
    fab.addEventListener("click", (e) => {
      if (justDragged) { e.preventDefault(); e.stopPropagation(); return; }
      if (typeof window.togglePiggy === "function") window.togglePiggy();
    });

    // Arrasto: SÓ no app/PWA. No desktop/site a bolinha fica fixa.
    if (!document.documentElement.classList.contains("pb-app")) return;

    const KEY = "pbFabPos", MARGIN = 14, THRESHOLD = 6;
    const size = () => fab.offsetWidth || 58;
    // Deixa espaço pra tab bar embaixo e pro notch/status em cima.
    const RESERVE_BOTTOM = 92, MIN_TOP = 54;
    const clampTop = (y) => Math.max(MIN_TOP, Math.min(y, window.innerHeight - size() - RESERVE_BOTTOM));

    // setProperty com 'important' pra vencer as regras !important do app-mode.css
    function place(side, top) {
      fab.style.setProperty("top", clampTop(top) + "px", "important");
      fab.style.setProperty("bottom", "auto", "important");
      fab.style.setProperty(side === "left" ? "left" : "right", MARGIN + "px", "important");
      fab.style.setProperty(side === "left" ? "right" : "left", "auto", "important");
    }
    function save(side, top) { try { localStorage.setItem(KEY, JSON.stringify({ side, top })); } catch (_) {} }
    function load() { try { return JSON.parse(localStorage.getItem(KEY)); } catch (_) { return null; } }

    // Restaura posição salva (se houver)
    const saved = load();
    if (saved && (saved.side === "left" || saved.side === "right")) place(saved.side, saved.top);

    let active = false, moved = false, sx = 0, sy = 0, ox = 0, oy = 0;

    fab.addEventListener("pointerdown", (e) => {
      active = true; moved = false;
      const r = fab.getBoundingClientRect();
      sx = e.clientX; sy = e.clientY; ox = e.clientX - r.left; oy = e.clientY - r.top;
      try { fab.setPointerCapture(e.pointerId); } catch (_) {}
    });

    fab.addEventListener("pointermove", (e) => {
      if (!active) return;
      if (!moved && Math.hypot(e.clientX - sx, e.clientY - sy) < THRESHOLD) return;
      moved = true;
      fab.classList.add("pb-dragging");
      const s = size();
      const left = Math.max(4, Math.min(e.clientX - ox, window.innerWidth - s - 4));
      fab.style.setProperty("left", left + "px", "important");
      fab.style.setProperty("right", "auto", "important");
      fab.style.setProperty("top", clampTop(e.clientY - oy) + "px", "important");
      fab.style.setProperty("bottom", "auto", "important");
      e.preventDefault();
    });

    function end(e) {
      if (!active) return;
      active = false;
      try { fab.releasePointerCapture(e.pointerId); } catch (_) {}
      if (!moved) return; // toque curto → o click acima abre o chat
      fab.classList.remove("pb-dragging");
      const r = fab.getBoundingClientRect();
      const side = (r.left + r.width / 2) < window.innerWidth / 2 ? "left" : "right";
      // Anima o "encaixe" na lateral, depois solta a transição pro hover voltar
      fab.style.transition = "top .18s ease, left .18s ease, right .18s ease";
      place(side, r.top);
      save(side, r.top);
      setTimeout(() => { fab.style.transition = ""; }, 220);
      justDragged = true;
      setTimeout(() => { justDragged = false; }, 0);
    }
    fab.addEventListener("pointerup", end);
    fab.addEventListener("pointercancel", end);

    // Reposiciona ao virar a tela / redimensionar (re-clampa nos limites novos)
    window.addEventListener("resize", () => {
      const p = load();
      if (p && (p.side === "left" || p.side === "right")) place(p.side, p.top);
    });
  })();
})();
