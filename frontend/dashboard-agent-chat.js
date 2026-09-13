/* global _agentesCache: writable, _agentName, navigateTo, API, USER_ID, loadAgentesView */
/* Conversas especialistas. Estado só em memória: reload apaga tela e contexto. */
(function () {
  const sessions = new Map();
  let currentKind = null;
  let openGeneration = 0;
  let messageSequence = 0;
  let registered = false;
  const ui = window.PigBankChatUI;
  if (!ui) return;
  const questions = {
    xerife: 'Algum gasto fugiu do meu padrão?',
    detetive: 'Há lançamentos que parecem duplicados?',
    carteiro: 'Quais contas vencem nos próximos dias?',
    reporter: 'Como estão minhas entradas e saídas neste mês?',
    cofre: 'Quanto falta para minhas metas?',
    barao: 'O que considerar ao avaliar renda fixa?',
    faria_limer: 'Como está a concentração da minha carteira?',
  };

  function state(kind) {
    if (!sessions.has(kind)) sessions.set(kind, { messages: [], context: null, draft: '', busy: false, access: 'loading', error: '', usage: null });
    return sessions.get(kind);
  }
  function close() {
    ui.close('agent');
  }
  function upgrade() {
    close();
    showUpgradeModal('agents');
  }
  function view() {
    const kind = currentKind;
    const s = state(kind);
    const card = ((_agentesCache || {}).catalog || []).find(c => c.kind === kind);
    const name = card?.nome || _agentName(kind);
    const messages = s.messages.map(message => {
      const actions = [];
      if (message.state === 'error' && message === s.messages[s.messages.length - 1]) {
        if (message.errorCode === 'invalid_context') {
          actions.push({ label: 'Iniciar nova conversa', onClick: () => {
            const draft = s.draft || message.question;
            sessions.delete(kind);
            window.openAgentChat(kind, draft);
          } });
        } else if (message.retryable && s.access === 'ready') {
          actions.push({ label: 'Tentar novamente', onClick: () => sendTurn(kind, message.question, message), disabled: s.busy });
        }
      }
      for (const destination of message.redirects || []) {
        const label = destination.access === 'ready' ? `Conversar com ${destination.name}`
          : destination.access === 'activate' ? `Ativar ${destination.name} e conversar`
          : `Ver acesso a ${destination.name}`;
        actions.push({ label, onClick: () => window.openAgentChat(destination.kind, destination.question) });
      }
      return { ...message, actions, author: message.role === 'user' ? 'Você'
        : message.state === 'error' ? 'Resposta não concluída' : name };
    });
    const actions = [];
    let note = '';
    if (s.access === 'loading') note = 'Verificando acesso ao agente…';
    if (s.access === 'activate') {
      note = s.busy ? 'Ativando agente…' : 'Ative este agente para conversar. A ativação ocupa energia do seu plano.';
      actions.push({ label: s.busy ? 'Ativando…' : 'Ativar e conversar', onClick: () => activate(kind), disabled: s.busy });
    } else if (s.access === 'upgrade' || s.access === 'no_energy') {
      note = s.access === 'no_energy' ? 'Falta energia para este agente. O plano Pro permite manter todos ativos.' : 'Seu plano não inclui a conversa com este agente.';
      actions.push({ label: 'Ver opções de plano', onClick: upgrade });
      if (s.access === 'no_energy') actions.push({ label: 'Gerenciar agentes', onClick: () => { close(); navigateTo('agentes'); } });
    }
    if (s.access === 'unavailable') note = 'Não foi possível verificar o acesso. Tente abrir a conversa novamente.';
    return {
      conversationId: kind, title: name, subtitle: 'Consultas e ideias sobre o meu tema',
      avatar: `/brand/agents/${kind}.png?v=3`, messages, draft: s.draft,
      disabled: s.busy || s.access !== 'ready', status: s.error || note, actions,
      emptyText: card?.desc || 'Posso ajudar com perguntas sobre meu tema.',
      suggestions: [{ label: questions[kind], onClick: () => {
        s.draft = questions[kind];
        render();
        ui.focusInput('agent');
      } }],
      usage: s.usage
        ? `${s.usage.used.toLocaleString('pt-BR')} de ${s.usage.limit.toLocaleString('pt-BR')} mensagens da cota compartilhada. Recarregar limpa a conversa.`
        : 'Cota compartilhada com o Piggy. Recarregar limpa a conversa.',
      onDraftChange: value => { s.draft = value; render(); },
      onSend: submit,
      onHidden: () => { ++openGeneration; },
    };
  }
  function render() {
    if (currentKind) ui.update('agent', view());
  }

  window.openAgentChat = async function (kind, question = '') {
    if (!Object.prototype.hasOwnProperty.call(questions, kind)) return;
    currentKind = kind;
    const generation = ++openGeneration;
    const ownsOpening = () => generation === openGeneration && currentKind === kind && ui.isOpen('agent');
    const s = state(kind);
    if (question) s.draft = question;
    s.access = 'loading';
    s.error = '';
    if (!registered) { ui.register('agent', view()); registered = true; }
    else render();
    ui.open('agent');
    try {
      const response = await fetch(`${API}/agents/${USER_ID}`, { credentials: 'same-origin' });
      const data = await response.json();
      // Fechar, trocar ou reabrir o painel invalida esta leitura inteira,
      // inclusive o cache compartilhado e o estado que submit consulta.
      if (!ownsOpening()) return;
      if (!response.ok) throw new Error('Não foi possível abrir o agente. Tente novamente.');
      _agentesCache = { ...data, events: _agentesCache?.events || [] };
      const card = data.catalog.find(c => c.kind === kind);
      const used = Number(data.energy_used || 0);
      const budget = Number(data.energy_budget || 0);
      s.access = !card?.disponivel ? 'unavailable'
        : data.can_chat === false || data.can_activate === false ? 'upgrade'
        : data.energy_enabled && (used > budget || (card.status !== 'active' && used + card.energy_cost > budget)) ? 'no_energy'
        : card.status === 'active' ? 'ready' : 'activate';
    } catch (error) {
      if (!ownsOpening()) return;
      s.access = 'unavailable';
      s.error = error.message;
    }
    if (ownsOpening()) {
      render();
      ui.focusInput('agent');
    }
  };

  async function activate(kind) {
    const s = state(kind);
    if (s.busy) return;
    s.busy = true;
    render();
    try {
      const response = await fetch(`${API}/agents/${USER_ID}/${kind}/activate`, {
        method: 'POST', credentials: 'same-origin', headers: csrfHeaders({ 'Content-Type': 'application/json' }), body: '{}',
      });
      const data = await response.json();
      if (!response.ok) {
        s.access = data.detail?.error === 'no_energy' ? 'no_energy' : data.detail?.error === 'pro_required' ? 'upgrade' : 'unavailable';
        s.error = s.access === 'unavailable' ? 'Não foi possível ativar o agente. Tente novamente.' : '';
      } else {
        s.access = 'ready';
        s.error = '';
        await loadAgentesView(true);
      }
    } catch (_error) {
      s.error = 'Não foi possível ativar o agente. Tente novamente.';
    } finally {
      s.busy = false;
      if (currentKind === kind) { render(); if (ui.isOpen('agent')) ui.focusInput('agent'); }
    }
  }

  function submit() {
    const kind = currentKind;
    if (!kind) return;
    const s = state(kind);
    const text = s.draft.trim();
    const last = s.messages[s.messages.length - 1];
    const retry = last?.state === 'error' && last.retryable && last.question === text ? last : null;
    sendTurn(kind, text, retry);
  }

  async function sendTurn(kind, text, reply = null) {
    const s = state(kind);
    if (s.busy || s.access !== 'ready' || !text || text.length > 2000) return;
    if (reply && (reply !== s.messages[s.messages.length - 1] || reply.state !== 'error')) return;
    if (!reply || s.draft === text) s.draft = '';
    s.busy = true;
    s.error = '';
    if (!reply) {
      s.messages.push({ id: `agent-${++messageSequence}`, role: 'user', content: text });
      reply = { id: `agent-${++messageSequence}`, role: 'assistant', question: text };
      s.messages.push(reply);
    }
    Object.assign(reply, { state: 'pending', content: 'Preparando a resposta…', errorCode: '', redirects: [] });
    render();
    try {
      const response = await fetch(`${API}/agents/${USER_ID}/${kind}/chat`, {
        method: 'POST', credentials: 'same-origin', headers: csrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ message: text, context: s.context }),
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || typeof data?.reply !== 'string' || !data.reply.trim()) {
        const detail = data?.detail || {};
        const fallback = response.status === 429
          ? 'Muitas mensagens em pouco tempo. Aguarde um instante antes de tentar novamente.'
          : 'Não foi possível obter a resposta agora. Tente novamente em instantes.';
        throw Object.assign(new Error(typeof detail.message === 'string' ? detail.message : fallback), {
          chatError: true, code: detail.error,
          retryable: detail.retryable ?? ![400, 401, 403, 404, 422].includes(response.status),
        });
      }
      s.context = data.context;
      s.usage = data.usage;
      Object.assign(reply, { state: 'complete', content: data.reply, redirects: data.redirects || [] });
      // Sincroniza o contador visível dos agentes sem compartilhar conversas.
      for (const other of sessions.values()) other.usage = data.usage;
    } catch (error) {
      Object.assign(reply, {
        state: 'error', errorCode: error.code,
        content: error.chatError ? error.message : 'A conexão foi interrompida antes de recebermos a resposta. Sua pergunta continua aqui.',
        retryable: error.chatError ? error.retryable : true,
      });
      if (['activate', 'upgrade', 'no_energy'].includes(error.code)) s.access = error.code;
      if (!s.draft) s.draft = text;
    } finally {
      s.busy = false;
      if (currentKind === kind) { render(); if (ui.isOpen('agent')) ui.focusInput('agent'); }
    }
  }
  document.getElementById('agentes-shelf')?.addEventListener('click', event => {
    const trigger = event.target.closest('[data-agent-chat]');
    if (trigger) window.openAgentChat(trigger.dataset.agentChat);
  });
})();
