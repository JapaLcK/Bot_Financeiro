/* global _agentesCache: writable, _agentName, navigateTo, API, USER_ID, loadAgentesView */
/* Conversas especialistas. Estado só em memória: reload apaga tela e contexto. */
(function () {
  const sessions = new Map();
  let currentKind = null;
  let opener = null;
  let openGeneration = 0;
  const panel = document.getElementById('agent-chat-panel');
  if (!panel) return;
  const title = document.getElementById('agent-chat-title');
  const subtitle = document.getElementById('agent-chat-subtitle');
  const avatar = document.getElementById('agent-chat-avatar');
  const log = document.getElementById('agent-chat-log');
  const input = document.getElementById('agent-chat-input');
  const send = document.getElementById('agent-chat-send');
  const status = document.getElementById('agent-chat-status');
  const actions = document.getElementById('agent-chat-actions');
  const usage = document.getElementById('agent-chat-usage');
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
  function button(label, callback) {
    const el = document.createElement('button');
    el.type = 'button';
    el.className = 'agent-chat-action';
    el.textContent = label;
    el.addEventListener('click', callback);
    return el;
  }
  function saveDraft() {
    if (currentKind) state(currentKind).draft = input.value;
  }
  function close() {
    saveDraft();
    ++openGeneration;
    panel.hidden = true;
    if (opener?.isConnected) opener.focus();
  }
  function upgrade() {
    close();
    showUpgradeModal('agents');
  }
  function render() {
    if (!currentKind) return;
    const s = state(currentKind);
    const card = ((_agentesCache || {}).catalog || []).find(c => c.kind === currentKind);
    title.textContent = card?.nome || _agentName(currentKind);
    subtitle.textContent = 'Consultas e ideias sobre o meu tema';
    avatar.src = `/brand/agents/${currentKind}.png?v=3`;
    log.replaceChildren();
    if (!s.messages.length) {
      const empty = document.createElement('div');
      empty.className = 'agent-chat-empty';
      const intro = document.createElement('p');
      intro.textContent = card?.desc || 'Posso ajudar com perguntas sobre meu tema.';
      empty.append(intro, button(questions[currentKind] || 'Como você pode me ajudar?', () => {
        input.value = questions[currentKind] || 'Como você pode me ajudar?';
        s.draft = input.value;
        input.focus();
      }));
      log.append(empty);
    }
    for (const message of s.messages) {
      const row = document.createElement('div');
      row.className = `agent-chat-message agent-chat-${message.role}`;
      row.dataset.state = message.state || 'complete';
      if (message.state === 'pending') row.setAttribute('aria-busy', 'true');
      const author = document.createElement('b');
      author.textContent = message.role === 'user' ? 'Você' : message.state === 'error' ? 'Resposta não concluída' : title.textContent;
      const body = document.createElement('p');
      body.textContent = message.content;
      row.append(author, body);
      if (message.state === 'error' && message === s.messages.at(-1)) {
        const kind = currentKind;
        if (message.errorCode === 'invalid_context') {
          row.append(button('Iniciar nova conversa', () => {
            const draft = s.draft || message.question;
            sessions.delete(kind);
            window.openAgentChat(kind, draft);
          }));
        } else if (message.retryable && s.access === 'ready') {
          const retry = button('Tentar novamente', () => sendTurn(kind, message.question, message));
          retry.disabled = s.busy;
          row.append(retry);
        }
      }
      for (const destination of message.redirects || []) {
        const label = destination.access === 'ready' ? `Conversar com ${destination.name}`
          : destination.access === 'activate' ? `Ativar ${destination.name} e conversar`
          : `Ver acesso a ${destination.name}`;
        row.append(button(label, () => window.openAgentChat(destination.kind, destination.question)));
      }
      log.append(row);
    }
    actions.replaceChildren();
    let note = '';
    if (s.access === 'loading') note = 'Verificando acesso ao agente…';
    if (s.access === 'activate') {
      note = s.busy ? 'Ativando agente…' : 'Ative este agente para conversar. A ativação ocupa energia do seu plano.';
      const kind = currentKind;
      const activateButton = button(s.busy ? 'Ativando…' : 'Ativar e conversar', () => activate(kind));
      activateButton.disabled = s.busy;
      actions.append(activateButton);
    } else if (s.access === 'upgrade' || s.access === 'no_energy') {
      note = s.access === 'no_energy' ? 'Falta energia para este agente. O plano Pro permite manter todos ativos.' : 'Seu plano não inclui a conversa com este agente.';
      actions.append(button('Ver opções de plano', upgrade));
      if (s.access === 'no_energy') actions.append(button('Gerenciar agentes', () => { close(); navigateTo('agentes'); }));
    }
    if (s.access === 'unavailable') note = 'Não foi possível verificar o acesso. Tente abrir a conversa novamente.';
    status.textContent = s.error || note;
    input.disabled = s.busy || s.access !== 'ready';
    send.disabled = input.disabled;
    input.value = s.draft;
    usage.textContent = s.usage
      ? `${s.usage.used.toLocaleString('pt-BR')} de ${s.usage.limit.toLocaleString('pt-BR')} mensagens da cota compartilhada. Recarregar limpa a conversa.`
      : 'Cota compartilhada com o Piggy. Recarregar limpa a conversa.';
    log.scrollTop = log.scrollHeight;
  }

  window.openAgentChat = async function (kind, question = '') {
    if (!Object.hasOwn(questions, kind)) return;
    saveDraft();
    if (panel.hidden) opener = document.activeElement;
    currentKind = kind;
    const generation = ++openGeneration;
    const ownsOpening = () => generation === openGeneration && currentKind === kind && !panel.hidden;
    const s = state(kind);
    if (question) s.draft = question;
    s.access = 'loading';
    s.error = '';
    panel.hidden = false;
    if (window.closePiggy) window.closePiggy();
    render();
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
      if (!input.disabled) input.focus();
      else document.getElementById('agent-chat-close').focus();
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
      if (currentKind === kind) { render(); if (!panel.hidden && !input.disabled) input.focus(); }
    }
  }

  function submit(event) {
    event.preventDefault();
    const kind = currentKind;
    if (!kind) return;
    const s = state(kind);
    const text = input.value.trim();
    const last = s.messages.at(-1);
    const retry = last?.state === 'error' && last.retryable && last.question === text ? last : null;
    sendTurn(kind, text, retry);
  }

  async function sendTurn(kind, text, reply = null) {
    const s = state(kind);
    if (s.busy || s.access !== 'ready' || !text || text.length > 2000) return;
    if (reply && (reply !== s.messages.at(-1) || reply.state !== 'error')) return;
    if (!reply || s.draft === text) s.draft = '';
    s.busy = true;
    s.error = '';
    if (!reply) {
      s.messages.push({ role: 'user', content: text });
      reply = { role: 'assistant', question: text };
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
      if (currentKind === kind) { render(); if (!panel.hidden && !input.disabled) input.focus(); }
    }
  }
  document.getElementById('agentes-shelf')?.addEventListener('click', event => {
    const trigger = event.target.closest('[data-agent-chat]');
    if (trigger) window.openAgentChat(trigger.dataset.agentChat);
  });
  document.getElementById('agent-chat-close').addEventListener('click', close);
  document.getElementById('agent-chat-form').addEventListener('submit', submit);
  input.addEventListener('input', saveDraft);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) submit(event);
  });
  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') { event.stopPropagation(); close(); }
  });
})();
