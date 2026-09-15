/* Conferência explícita: a declaração e o extrato precisam representar o mesmo fato. */
(function () {
  let overlay;
  let previousFocus;
  let activeUser;
  let afterSave;
  const money = value => value == null ? "Valor líquido a conferir" : new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
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
  async function load() {
    const content = overlay.querySelector("[data-movements]");
    content.replaceChildren(element("p", "Carregando declarações…", "msub"));
    try {
      const response = await fetch(`/open-finance/${activeUser}/movements`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (!overlay) return;
      content.replaceChildren();
      if (!data.movements?.length) content.append(element("p", "Nenhuma declaração aguardando confirmação.", "msub"));
      for (const movement of data.movements || []) {
        const section = element("section", null, "modal-row");
        section.style.cssText = "border-top:1px solid var(--glass-border);padding-top:16px;overflow-wrap:anywhere";
        section.append(element("h4", movement.name), element("p", `${money(movement.amount)} · ${movement.bank || "Banco a conferir"} · ${movement.date}`, "msub"));
        section.append(element("p", "Não confirmado no extrato", "modal-label"));
        if (!movement.candidates.length) {
          section.append(element("p", "Ainda não há transação compatível. Confira o extrato do banco; sincronizar sem essa transação não confirma a declaração.", "msub"));
        } else {
          const label = element("label", "Transação no extrato", "modal-label");
          const select = element("select", null, "modal-inp");
          select.id = `bank-movement-${movement.launch_id}`;
          label.htmlFor = select.id;
          select.append(new Option("Selecione a mesma movimentação", ""));
          for (const tx of movement.candidates) select.append(new Option(`${tx.date} · ${money(tx.amount)} · ${tx.bank} · ${tx.description}`, String(tx.id)));
          const button = element("button", "Conferir vínculo", "btn-save");
          button.type = "button";
          button.disabled = true;
          button.style.marginTop = "12px";
          select.addEventListener("change", () => { button.disabled = !select.value; });
          button.addEventListener("click", async () => {
            const tx = movement.candidates.find(x => String(x.id) === select.value);
            if (!tx) return;
            overlay.classList.remove("open");
            const confirmed = await window.confirmModal(
              `Declaração: ${movement.name}, ${money(movement.amount)}, ${movement.date}.\n\nExtrato: ${tx.description}, ${money(tx.amount)}, ${tx.date}, ${tx.bank}.\n\nConfirma que são a mesma movimentação já feita no banco?`,
              { title: "Conferir com o extrato", okText: "São a mesma movimentação" });
            if (!overlay) return;
            overlay.classList.add("open");
            button.focus();
            if (!confirmed) return;
            button.disabled = true;
            try {
              const saved = await fetch(`/open-finance/${activeUser}/movements/confirm`, {
                method: "POST", credentials: "same-origin", headers: csrfHeaders({ "Content-Type": "application/json" }),
                body: JSON.stringify({ launch_id: movement.launch_id, transaction_id: tx.id }) });
              if (!saved.ok) throw new Error();
              await load();
              if (afterSave) await afterSave("Conferência registrada.");
            } catch (_) {
              button.disabled = false;
              await window.alertModal("Não foi possível vincular os registros. Atualize a lista e confira novamente.");
            }
          });
          section.append(label, select, button);
        }
        content.append(section);
      }
    } catch (_) {
      if (overlay) content.replaceChildren(element("p", "Não foi possível carregar. Feche e tente novamente.", "msub"));
    }
  }
  window.pigModalKeys("bank-movements-overlay", close);
  function open(userId, onSave) {
    if (overlay) return;
    activeUser = userId;
    afterSave = onSave;
    previousFocus = document.activeElement;
    overlay = element("div", null, "overlay open");
    overlay.id = "bank-movements-overlay";
    const modal = element("div", null, "modal wide");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "bank-movements-title");
    const title = element("h3", "Movimentações bancárias");
    title.id = "bank-movements-title";
    const content = element("div");
    content.dataset.movements = "";
    const button = element("button", "Fechar", "btn-cancel");
    button.type = "button";
    button.addEventListener("click", close);
    const actions = element("div", null, "modal-acts");
    actions.append(button);
    modal.append(title, element("p", "O PigBank registra o que você declarou. A confirmação exige conferir a mesma operação no extrato; o saldo do banco não é alterado aqui.", "msub"), content, actions);
    overlay.append(modal);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.body.append(overlay);
    button.focus();
    void load();
  }
  window.BankMovements = { open, close };
}());
