/* global _agentesCache: writable, API, USER_ID, csrfHeaders, readResponsePayload, showToast, showUpgradeModal, loadAgentesView */
/* "Era esperado" no feed do Xerife (PL-04): tira UM lançamento do alerta e da média dele.
   Delegação de evento; o botão é desenhado por _renderAgentes (dashboard.js). */
(function () {
  const FEED = "agentes-feed";

  // Tira o item do feed e do cache da view, por id, e devolve o foco sem roubá-lo de quem digitou em
  // outro campo enquanto o PUT voava.
  function tirarDoFeed(btn, id) {
    const feed = document.getElementById(FEED);
    const botoes = [...(feed ? feed.querySelectorAll("[data-esperado-lancamento]") : [])];
    const i = botoes.indexOf(btn);              // -1: o feed foi redesenhado durante o PUT
    const vizinho = botoes[i + 1] || botoes[i - 1];
    // Por id, não pelo botão: se o feed foi redesenhado durante o PUT, o botão antigo já saiu do DOM.
    feed?.querySelectorAll(`[data-esperado-lancamento="${id}"]`).forEach(b => b.closest(".ag-event")?.remove());
    // Foco não cai no BODY: vai para o próximo "Era esperado" ou, sem ele, para o container do feed.
    const foco = document.activeElement;
    if (feed && i >= 0 && (!foco || foco === btn || foco === document.body)) {
      feed.tabIndex = -1;
      (vizinho || feed).focus();
    }
    if (_agentesCache && _agentesCache.events) {
      _agentesCache.events = _agentesCache.events.filter(ev => (ev.payload || {}).launch_id !== id);
    }
    if (feed && !feed.querySelector(".ag-event")) loadAgentesView(true);
  }

  async function marcar(btn) {
    const id = Number(btn.dataset.esperadoLancamento);
    if (!Number.isInteger(id) || btn.disabled) return;
    btn.disabled = true;
    try {
      const res = await fetch(`${API}/agents/${USER_ID}/xerife/lancamentos/${id}/esperado`, {
        method: "PUT", credentials: "same-origin",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ esperado: true }),
      });
      const data = await readResponsePayload(res);
      const detail = data.detail || {};
      if (res.status === 403 && detail.error === "pro_required") return showUpgradeModal("agents");
      // 404 do lançamento (apagado): o alerta é órfão. O 404 de "Feature indisponível" (fora do beta) não é.
      if (res.status === 404 && detail === "Lançamento não encontrado.") {
        tirarDoFeed(btn, id);
        return showToast("Esse gasto não existe mais; tirei o alerta.");
      }
      if (!res.ok) throw new Error(typeof detail === "string" ? detail : "falhou");
      // Só depois do 200: o item sai da tela e do cache da view.
      tirarDoFeed(btn, id);
      showToast("✓ Pronto, esse gasto não conta mais na sua média.");
    } catch {
      showToast("Não deu pra marcar agora. Tente de novo.");
    } finally {
      btn.disabled = false;
    }
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest("[data-esperado-lancamento]");
    if (btn) marcar(btn);
  });
})();
