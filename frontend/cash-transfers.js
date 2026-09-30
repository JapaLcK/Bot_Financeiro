/* Saque e depósito em espécie do Open Finance (Q41): os avisos ("somamos na
   sua Carteira") e as perguntas. A porta é a faixa de avisos do /app
   (dashboard.js, `renderAlerts`); as regras são do servidor
   (db/open_finance_cash_answers.py). Molde: reconciliations.js.
   Dado do servidor entra só por textContent. */
(function () {
  if (window.CashTransfers) return;
  let overlay;
  let previousFocus;
  let activeUser;
  let afterSave;
  const TIPO = { saque: "Saque", deposito: "Depósito", fraco: "Pix Saque" };
  const NAO_ERA = ["Não era dinheiro vivo", "not_cash"];
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  // "2026-09-10" → "10/09", sem Date (fuso não muda o dia).
  function ddmm(iso) {
    const [, m, d] = String(iso || "").split("-");
    return m && d ? `${d.slice(0, 2)}/${m}` : "";
  }
  function close() {
    overlay?.remove();
    overlay = null;
    previousFocus?.focus();
  }
  async function act(id, action) {
    const response = await fetch(`/open-finance/${activeUser}/cash-transfers/${id}/${action}`, {
      method: "POST", credentials: "same-origin", headers: csrfHeaders(),
    });
    if (response.ok) return response.json();
    let detail = "Não foi possível concluir. Atualize a lista e confira novamente.";
    try { detail = (await response.json()).detail || detail; } catch (_) { /* corpo sem JSON */ }
    const err = new Error(detail);
    err.status = response.status;
    throw err;
  }
  // [frase, [[rótulo, ação], ...]] de cada estado. O 1º botão é o principal.
  // "Não era dinheiro vivo" vale em toda pergunta de depósito e Pix Saque
  // (db/open_finance_cash_answers.py::respostas); o saque é sempre dinheiro.
  function pergunta(r) {
    const dep = r.kind === "deposito";
    const valor = fmt(r.amount);
    if (r.status === "ativo") {
      return [dep ? `Tiramos ${valor} da sua Carteira.` : `Somamos ${valor} na sua Carteira.`,
        [["Ok", "seen"], ["Desfazer", "undo"]]];
    }
    if (r.status === "perguntar_fraco") {
      return [dep ? "Foi dinheiro vivo que você depositou?" : "Você sacou dinheiro vivo com esse Pix?",
        [[dep ? "Sim, tira da Carteira" : "Sim, soma na Carteira", "cash"], NAO_ERA]];
    }
    const extra = r.kind === "saque" ? [] : [NAO_ERA];
    if (r.status === "perguntar_novo") {
      return [dep ? "Você já tirou esse dinheiro da Carteira?" : "Você já anotou esse dinheiro na Carteira?",
        [[dep ? "Já tirei" : "Já anotei", "already"], [dep ? "Não, tira da Carteira" : "Não, soma na Carteira", "credit"], ...extra]];
    }
    return [`Você anotou “${r.manual_alvo || "—"}”, ${fmt(r.manual_valor)} em ${ddmm(r.manual_date)}. É o mesmo dinheiro?`,
      [["É o mesmo", "same"], [dep ? "São diferentes, tira da Carteira" : "São diferentes, soma na Carteira", "different"], ...extra]];
  }
  // Overlay fechado durante o POST: se deu certo, o dashboard ainda atualiza;
  // se falhou, não há diálogo para alertar. `buttons`: todos os da MESMA linha
  // (o segundo não pode disparar por cima do primeiro em voo).
  async function _run(id, action, buttons) {
    buttons.forEach(b => { b.disabled = true; });
    try {
      await act(id, action);
      if (overlay) await load();
      if (afterSave) await afterSave();
    } catch (err) {
      buttons.forEach(b => { b.disabled = false; });
      if (!overlay) return;
      if (err.status !== 404) await window.alertModal(err.message);  // 404: já resolvido noutra aba
      await load();
    }
  }
  async function _undo(id, button, buttons) {
    overlay.classList.remove("open");
    const confirmed = await window.confirmModal(
      "A Carteira volta a como estava. O saque continua fora dos seus gastos.",
      { title: "Desfazer", okText: "Desfazer", destructive: true, danger: true });
    if (!overlay) return;
    overlay.classList.add("open");
    button.focus();
    if (confirmed) _run(id, "undo", buttons);
  }
  function _row(r) {
    const section = element("div", null, "modal-row");
    section.style.cssText = "border-top:1px solid var(--glass-border);padding-top:16px;overflow-wrap:anywhere";
    const linha = [`${TIPO[r.kind] || "Saque"} de ${fmt(r.amount)}`, r.institution, ddmm(r.tx_date)];
    section.append(element("p", linha.filter(Boolean).join(" · "), "msub"));
    const [frase, opcoes] = pergunta(r);
    section.append(element("p", frase));
    const buttons = opcoes.map(([rotulo], i) => {
      const b = element("button", rotulo, i === 0 ? "btn-save" : "btn-cancel");
      b.type = "button";
      return b;
    });
    buttons.forEach((b, i) => {
      const action = opcoes[i][1];
      b.addEventListener("click", () => (action === "undo" ? _undo(r.id, b, buttons) : _run(r.id, action, buttons)));
    });
    const acts = element("div", null, "modal-acts");
    acts.style.cssText = "margin-top:12px;flex-wrap:wrap";
    acts.append(...buttons);
    section.append(acts);
    return section;
  }
  async function load() {
    const content = overlay.querySelector("[data-cash-transfers]");
    content.replaceChildren(element("p", "Carregando…", "msub"));
    try {
      const response = await fetch(`/open-finance/${activeUser}/cash-transfers`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (!overlay) return;
      const items = data.items || [];
      content.replaceChildren(...(items.length ? items.map(_row) : [element("p", "Nada para conferir.", "msub")]));
    } catch (_) {
      if (overlay) content.replaceChildren(element("p", "Não foi possível carregar. Feche e tente novamente.", "msub"));
    }
  }
  window.pigModalKeys("cash-transfers-overlay", close);
  function open(userId, onSave) {
    if (overlay) return;
    activeUser = userId;
    afterSave = onSave;
    previousFocus = document.activeElement;
    overlay = element("div", null, "overlay open");
    overlay.id = "cash-transfers-overlay";
    const modal = element("div", null, "modal wide");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "cash-transfers-title");
    const title = element("h3", "Dinheiro vivo");
    title.id = "cash-transfers-title";
    const content = element("div");
    content.dataset.cashTransfers = "";
    const button = element("button", "Fechar", "btn-cancel");
    button.type = "button";
    button.addEventListener("click", close);
    const actions = element("div", null, "modal-acts");
    actions.append(button);
    modal.append(title, element("p", "Saques e depósitos em dinheiro que vieram do seu banco. Eles mexem na Carteira, mas não contam como gasto nem receita.", "msub"), content, actions);
    overlay.append(modal);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.body.append(overlay);
    button.focus();
    void load();
  }
  window.CashTransfers = { open, close };
}());
