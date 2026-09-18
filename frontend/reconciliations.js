/* Reconciliação OF x lançamento manual: confirmar, rejeitar e desfazer a
   união entre uma transação do banco e um lançamento do PigBank.
   `aviso()` espelha core/services/funding.py::aviso_conferir (CLAUDE.md
   §0.7 — tests/fixtures/aviso_conferir.json é lida pelos dois lados). */
(function () {
  if (window.Reconciliations) return;
  let overlay;
  let previousFocus;
  let activeUser;
  let afterSave;
  // Mesmo formato de utils_text.fmt_brl: "R$ " sempre na frente, sinal DEPOIS
  // do "R$ " (Intl faz "-R$ 70,00"; fmt_brl faz "R$ -70,00" — a fixture prova
  // o segundo). Único formatador de dinheiro do arquivo — antes havia também
  // um `money()` via Intl, com o outro formato, lado a lado na mesma tela
  // (achado do Tester).
  function fmtBRL(v) {
    const n = Number(v) || 0;
    const [intPart, dec] = Math.abs(n).toFixed(2).split(".");
    const milhar = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return `R$ ${n < 0 ? "-" : ""}${milhar},${dec}`;
  }
  function aviso(exibido, rec) {
    const n = Number((rec || {}).pending_count) || 0;
    if (n <= 0) return "";
    let txt = `⚠ ${n} lançamento(s) a conferir`;
    const delta = Number((rec || {}).delta_se_confirmar) || 0;
    if (delta !== 0) txt += ` · pode ser ${fmtBRL(Number(exibido) + delta)}`;
    return txt;
  }
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function close() {
    overlay?.remove();
    overlay = null;
    previousFocus?.focus();
  }
  async function act(userId, ofTxId, action) {
    const response = await fetch(`/open-finance/${userId}/reconciliations/${ofTxId}/${action}`, {
      method: "POST", credentials: "same-origin", headers: csrfHeaders(),
    });
    if (response.ok) return response.json();
    let detail = "Não foi possível concluir. Atualize a lista e confira novamente.";
    try { detail = (await response.json()).detail || detail; } catch (_) { /* corpo sem JSON */ }
    const err = new Error(detail);
    err.status = response.status;
    throw err;
  }
  // Mesmo formatador que a linha "Banco:" (fmtBRL) — o sinal precisa entrar
  // NO NÚMERO passado a fmtBRL, não concatenado por fora, senão o sinal cai
  // antes do "R$ " (formato do Intl) em vez de depois dele (formato do
  // fmt_brl, achado do Tester: "-R$ 0,01" vs "R$ -0,01" no mesmo par).
  function launchAmount(l) {
    const valor = Math.abs(l.valor);
    return fmtBRL(l.tipo === "receita" ? valor : -valor);
  }
  function _side(r) {
    const section = element("div", null, "modal-row");
    section.style.cssText = "border-top:1px solid var(--glass-border);padding-top:16px;overflow-wrap:anywhere";
    section.append(element("p", `Banco: ${r.bank.description} · ${fmtBRL(r.bank.amount)} · ${r.bank.date} · ${r.bank.institution || "—"}`, "msub"));
    section.append(element("p", `PigBank: ${r.launch.alvo || r.launch.nota || "—"} · ${launchAmount(r.launch)} · ${r.launch.date}`, "msub"));
    return section;
  }
  // Overlay fechado durante o POST é conclusão válida, não erro (achado do
  // Tester/Codex): se deu certo, o dashboard ainda precisa atualizar (saldo e
  // aviso mudaram de verdade) mesmo sem lista para recarregar; se falhou, não
  // há diálogo para alertar nem lista para recarregar, e o dashboard não é
  // tocado — a ação não deu certo.
  // `buttons`: TODOS os botões de ação da MESMA linha (achado do Codex, P2) —
  // travar só o botão clicado deixava o outro (ex.: "São gastos diferentes")
  // disparar por cima de um confirm ainda em voo. Se o confirm commitar
  // primeiro, o servidor já não está mais "pending" e reject_reconciliation
  // trata isso como no-op de sucesso (db/reconciliation.py) — a UI parecia
  // aceitar a rejeição com o par continuando confirmado.
  async function _run(ofTxId, action, buttons) {
    buttons.forEach(b => { b.disabled = true; });
    try {
      await act(activeUser, ofTxId, action);
      if (overlay) await load();
      if (afterSave) await afterSave();
    } catch (err) {
      buttons.forEach(b => { b.disabled = false; });
      if (!overlay) return;
      if (err.status !== 404) await window.alertModal(err.message);
      await load();
    }
  }
  function _pendingRow(r) {
    const section = _side(r);
    const confirmBtn = element("button", "É o mesmo gasto", "btn-save");
    confirmBtn.type = "button";
    const rejectBtn = element("button", "São gastos diferentes", "btn-cancel");
    rejectBtn.type = "button";
    const rowButtons = [confirmBtn, rejectBtn];
    confirmBtn.addEventListener("click", () => _run(r.of_tx_id, "confirm", rowButtons));
    rejectBtn.addEventListener("click", async () => {
      overlay.classList.remove("open");
      const confirmed = await window.confirmModal(
        "Este par não volta a ser sugerido automaticamente. Confirma que são gastos diferentes?",
        { title: "Gastos diferentes", okText: "São diferentes" });
      if (!overlay) return;
      overlay.classList.add("open");
      rejectBtn.focus();
      if (!confirmed) return;
      _run(r.of_tx_id, "reject", rowButtons);
    });
    const acts = element("div", null, "modal-acts");
    acts.style.marginTop = "12px";
    acts.append(confirmBtn, rejectBtn);
    section.append(acts);
    return section;
  }
  function _fusedRow(r) {
    const section = _side(r);
    const undoBtn = element("button", "Desfazer", "btn-cancel");
    undoBtn.type = "button";
    undoBtn.addEventListener("click", async () => {
      overlay.classList.remove("open");
      const confirmed = await window.confirmModal(
        "Ao desfazer, este par não volta a ser sugerido automaticamente. Desfazer mesmo assim?",
        { title: "Desfazer união", okText: "Desfazer", destructive: true, danger: true });
      if (!overlay) return;
      overlay.classList.add("open");
      undoBtn.focus();
      if (!confirmed) return;
      _run(r.of_tx_id, "undo", [undoBtn]);
    });
    const acts = element("div", null, "modal-acts");
    acts.style.marginTop = "12px";
    acts.append(undoBtn);
    section.append(acts);
    return section;
  }
  async function load() {
    const content = overlay.querySelector("[data-reconciliations]");
    content.replaceChildren(element("p", "Carregando…", "msub"));
    try {
      const response = await fetch(`/open-finance/${activeUser}/reconciliations`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (!overlay) return;
      content.replaceChildren();
      const rows = data.reconciliations || [];
      const pending = rows.filter(r => r.status === "pending");
      const fused = rows.filter(r => r.status !== "pending");
      content.append(element("h4", "A conferir"));
      if (!pending.length) content.append(element("p", "Nada pendente de conferência.", "msub"));
      for (const r of pending) content.append(_pendingRow(r));
      content.append(element("h4", "Unidos nos últimos 60 dias"));
      if (!fused.length) content.append(element("p", "Nenhuma união recente.", "msub"));
      for (const r of fused) content.append(_fusedRow(r));
    } catch (_) {
      if (overlay) content.replaceChildren(element("p", "Não foi possível carregar. Feche e tente novamente.", "msub"));
    }
  }
  window.pigModalKeys("reconciliations-overlay", close);
  function open(userId, onSave) {
    if (overlay) return;
    activeUser = userId;
    afterSave = onSave;
    previousFocus = document.activeElement;
    overlay = element("div", null, "overlay open");
    overlay.id = "reconciliations-overlay";
    const modal = element("div", null, "modal wide");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "reconciliations-title");
    const title = element("h3", "Conferência com o extrato");
    title.id = "reconciliations-title";
    const content = element("div");
    content.dataset.reconciliations = "";
    const button = element("button", "Fechar", "btn-cancel");
    button.type = "button";
    button.addEventListener("click", close);
    const actions = element("div", null, "modal-acts");
    actions.append(button);
    modal.append(title, element("p", "Compare o que o banco registrou com o que o PigBank registrou. Confirmar une os dois; desfazer solta o lançamento e ele volta a contar na Carteira.", "msub"), content, actions);
    overlay.append(modal);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.body.append(overlay);
    button.focus();
    void load();
  }
  window.Reconciliations = { open, close, act, aviso };
}());
