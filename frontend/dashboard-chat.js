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
    'Como está minha carteira no Open Finance?',
  ];

  function asksAboutPortfolio(text) {
    const subject = /caixinh|investiment|carteira|renda fixa|renda vari[aá]vel|a[cç][oõ]es|ativos|cdb|tesouro|\bfiis?\b/i;
    const action = /\b(quanto|quais|como|mostr\w*|list\w*|detalh\w*|ver|veja|tenho|saldo|carteira|posi[cç][aã]o)\b/i;
    const directList = /\b(?:meu|minha|meus|minhas)\s+(?:pr[oó]prios?\s+)?(?:investimentos?|ativos?|carteira|caixinhas?|cdbs?|tesouros?|renda\s+(?:fixa|vari[aá]vel)|a[cç][oõ]es|fiis?)\b/i;
    const personal = /\b(?:meu|minha|meus|minhas|tenho|possuo|saldo|posi[cç][aã]o)\b/i;
    const openFinance = /\bopen finance\b/i;
    const unrelatedWallet = /\bcarteira\s+(?:de\s+)?(?:motorista|habilita[cç][aã]o|trabalho|estudante|vacina)\b/i;
    const unrelatedSubject = /\ba[cç][oõ]es\s+(?:judiciais|judici[aá]rias|penais|civis|trabalhistas)\b|\bativos?\s+(?:de\s+)?(?:software|ti|inform[aá]tica)\b/i;
    return !unrelatedWallet.test(text) && !unrelatedSubject.test(text) && subject.test(text)
      && (directList.test(text) || openFinance.test(text) || (personal.test(text) && action.test(text)));
  }

  const formatMoney = value => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);

  function answerPortfolioQuestion(question, portfolio) {
    if (busy || !portfolio) return;
    const items = portfolio.groups.flatMap(group => group.items);
    let content;
    if (/cdb/i.test(question)) {
      const cdbs = items.filter(item => item.subtype === 'CDB' || /\bcdb\b/i.test(item.name))
        .sort((a, b) => b.amount - a.amount).slice(0, 3);
      content = cdbs.length
        ? `Seus maiores CDBs compartilhados pelo Open Finance são:\n\n${cdbs.map((item, index) => `${index + 1}. **${item.name}** — ${formatMoney(item.amount)} (${item.institution})`).join('\n')}`
        : 'Não encontrei CDBs entre os investimentos compartilhados pelo Open Finance.';
    } else {
      const variableIncome = portfolio.groups.find(group => group.title === 'Ações e FIIs');
      if (!variableIncome?.items.length) {
        content = 'Não encontrei ações ou FIIs entre os investimentos compartilhados pelo Open Finance.';
      } else {
        const largest = [...variableIncome.items].sort((a, b) => b.amount - a.amount).slice(0, 3);
        content = `Sua renda variável compartilhada pelo Open Finance soma **${formatMoney(variableIncome.amount)}** em ${variableIncome.items.length} ${variableIncome.items.length === 1 ? 'ativo' : 'ativos'}.\n\nMaiores posições:\n${largest.map((item, index) => `${index + 1}. **${item.name}** — ${formatMoney(item.amount)} (${item.institution})`).join('\n')}`;
      }
    }
    messages.push({ id: `piggy-${++sequence}`, role: 'user', content: question });
    messages.push({ id: `piggy-${++sequence}`, role: 'assistant', author: 'Open Finance', content, state: 'complete', markdown: true });
    render();
  }

  function portfolioFromSnapshot(data) {
    const items = (Array.isArray(data.investments) ? data.investments : [])
      .filter(item => (item.currency || 'BRL').toUpperCase() === 'BRL'
        && Number.isFinite(Number(item.balance)) && Number(item.balance) > 0)
      .map(item => ({ name: item.name || item.subtype || 'Investimento',
        institution: item.institution_name || 'Instituição não informada',
        subtype: item.subtype || '', type: (item.type || '').toUpperCase(), amount: Number(item.balance) }));
    const definitions = [
      ['Renda fixa', item => item.type === 'FIXED_INCOME'],
      ['Ações e FIIs', item => item.type === 'EQUITY'],
      ['Outros ativos', item => !['FIXED_INCOME', 'EQUITY'].includes(item.type)],
    ];
    return {
      groups: definitions.map(([title, matches]) => {
        const assets = items.filter(matches);
        return { title, items: assets, amount: assets.reduce((sum, item) => sum + item.amount, 0) };
      }).filter(group => group.items.length),
      count: items.length,
      amount: items.reduce((sum, item) => sum + item.amount, 0),
      note: items.some(item => /nubank|nu financeira/i.test(item.institution) && item.subtype.toUpperCase() === 'CDB')
        ? 'Saldos da última sincronização. CDBs do Nubank podem incluir caixinhas; o Open Finance não identifica o apelido de cada uma.'
        : 'Saldos da última sincronização do Open Finance. A lista não inclui investimentos cadastrados manualmente.',
    };
  }

  async function loadPortfolio(message) {
    try {
      if (typeof USER_ID === 'undefined' || !Number.isInteger(Number(USER_ID)) || Number(USER_ID) <= 0) throw new Error('Usuário indisponível');
      const response = await fetch(`/open-finance/${USER_ID}`, { credentials: 'same-origin' });
      if (!response.ok) throw new Error(`Open Finance: ${response.status}`);
      const portfolio = portfolioFromSnapshot(await response.json());
      Object.assign(message, { state: 'complete', content: portfolio.count
        ? 'Carteira conectada · Open Finance'
        : 'Nenhum investimento em reais foi compartilhado pelo Open Finance até agora.',
        portfolio: portfolio.count ? portfolio : undefined });
    } catch (error) {
      console.warn('[piggy] carteira Open Finance:', error);
      Object.assign(message, { state: 'error', content: 'Não consegui carregar a carteira do Open Finance agora. Tente novamente mais tarde.' });
    }
    render();
  }

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
      onPortfolioAsk: answerPortfolioQuestion,
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
    if (asksAboutPortfolio(text)) {
      const card = { id: `piggy-${++sequence}`, role: 'assistant', author: 'Open Finance',
        content: 'Carregando carteira do Open Finance…', state: 'pending' };
      messages.push(card);
      render();
      await loadPortfolio(card);
      busy = false;
      render();
      if (ui.isOpen('piggy')) ui.focusInput('piggy');
      return;
    }
    const reply = { id: `piggy-${++sequence}`, role: 'assistant', content: 'Preparando a resposta…', createdAt, state: 'pending', markdown: true };
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
