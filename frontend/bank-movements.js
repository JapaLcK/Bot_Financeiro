/* Conferência explícita: a declaração e o extrato precisam representar o mesmo fato. */
(function () {
  let overlay, previousFocus, activeUser, afterSave, launchId, closeButton, walletBalance;
  const brl = value => new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value);
  const money = value => value == null ? "Valor líquido a conferir" : brl(value);
  const orBlank = value => String(value ?? "").trim() || "sem descrição";  // alvo NULL/vazio (ex.: "gastei 50") não vira "null"
  const txText = tx => `${tx.date} · ${money(tx.amount)} · ${tx.bank} · ${tx.description}`;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function button(text, className) {
    const node = element("button", text, className);
    node.type = "button";
    return node;
  }
  function row(...buttons) {
    const node = element("div");
    node.style.cssText = "display:flex;flex-wrap:wrap;gap:8px;margin-top:12px";
    node.append(...buttons);
    return node;
  }
  function close() {
    overlay?.remove();
    overlay = null;
    previousFocus?.focus();
  }
  // Confirma, envia uma vez (botão travado do clique até a resposta) e recarrega.
  // 404 = já resolvido noutra aba ou apagado: recarrega e atualiza o Histórico, sem alerta.
  async function act(btn, { message, options, path, body, done }) {
    if (btn.disabled) return;
    btn.disabled = true;
    overlay.classList.remove("open");
    const confirmed = await window.confirmModal(message, options);
    if (!overlay) return;
    overlay.classList.add("open");
    if (!confirmed) {
      btn.disabled = false;
      btn.focus();
      return;
    }
    closeButton.focus();
    try {
      const response = await fetch(`/open-finance/${activeUser}/movements/${path}`, {
        method: "POST", credentials: "same-origin", headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body) });
      if (response.status !== 404 && !response.ok) throw new Error();
      await load();
      if (afterSave) await afterSave(response.status === 404 ? undefined : done);
    } catch (_) {
      // Um overlay por vez: com este aberto, o alerta (mesmo z-index, anterior no DOM) ficaria por baixo.
      overlay?.classList.remove("open");
      await window.alertModal("Não foi possível concluir. Atualize a lista e confira novamente.");
      if (!overlay) return;
      overlay.classList.add("open");
      await load();
    }
  }
  // Um grupo (label + select + botão logo abaixo dele, `role=group` nomeado pela label): com dois selects no
  // mesmo item (banco e Carteira) cada botão fica junto do SEU select. `click(btn, value)` decide a ação.
  function picker({ id, label: labelText, hint, placeholder, options, text, className, click }, out) {
    const group = element("div");
    const label = element("label", labelText, "modal-label");
    const select = element("select", null, "modal-inp");
    select.id = id;
    label.htmlFor = id;
    label.id = `${id}-label`;
    group.setAttribute("role", "group");
    group.style.marginTop = "32px";  // mais que o select->botão (28 px): o botão não pode parecer do grupo seguinte
    group.setAttribute("aria-labelledby", label.id);
    select.append(new Option(placeholder, ""));
    for (const [optionText, value] of options) select.append(new Option(optionText, value));
    const btn = button(text, className);
    btn.disabled = true;
    select.addEventListener("change", () => { btn.disabled = !select.value; });
    btn.addEventListener("click", () => click(btn, select.value));
    group.append(label, ...(hint ? [element("p", hint, "msub")] : []), select, row(btn));
    out.nodes.push(group);
  }
  // Select das transações do banco; `ask(tx)` monta a pergunta e `send(tx)` o corpo do POST.
  function txPicker(movement, { label, ask, send }, out) {
    picker({ id: `bank-movement-${movement.launch_id}`, label, placeholder: "Selecione a mesma movimentação",
      options: movement.candidates.map(tx => [txText(tx), String(tx.id)]),
      text: movement.match ? "Trocar a transação" : "Conferir vínculo", className: "btn-save",
      click: (btn, value) => {
        const tx = movement.candidates.find(x => String(x.id) === value);
        if (tx) void act(btn, { message: ask(tx), options: { title: "Conferir com o extrato", okText: "São a mesma movimentação" },
          path: "confirm", body: send(tx), done: "Conferência registrada." });
      } }, out);
  }
  // "Foi em dinheiro" (só fluxo sem par): escolher o lançamento da Carteira (sem novo débito) ou lançá-lo lá.
  function cashPicker(movement, declared, out) {
    const receita = movement.tipo === "receita", title = receita ? "Recebi em dinheiro" : "Foi em dinheiro";
    const cash = movement.cash.map(c => ({ ...c, name: orBlank(c.name), category: String(c.category ?? "").trim() }));
    const options = cash.map(c => [`${c.date} · ${brl(c.amount)} · ${c.name}${c.category ? ` · ${c.category}` : ""}`, String(c.id)]);
    options.push(["Nenhum destes: lançar na Carteira agora", "none"]);
    picker({ id: `bank-cash-${movement.launch_id}`, label: title, options, text: title, className: "btn-cancel",
      hint: `Use quando você ${receita ? "recebeu" : "pagou"} em dinheiro e não pelo banco.`,
      placeholder: "Escolha na sua Carteira",  // curto: em 375 px o select corta "Selecione o lançamento da Carteira"
      click: (btn, value) => {
        const chosen = cash.find(c => String(c.id) === value);
        const after = walletBalance + movement.amount;  // `amount` é assinado: gasto -V, receita +V
        const message = chosen
          ? `${declared}\n\nEla vai ficar registrada junto do lançamento da Carteira: ${chosen.date} · ${brl(chosen.amount)} · ${chosen.name}. Ela sai dos totais e o saldo da Carteira não muda.\n\nConfirma que ${receita ? "recebeu" : "foi"} em dinheiro?`
          : `${declared}\n\nVou lançar ${brl(Math.abs(movement.amount))} na Carteira, com a data da declaração (${movement.date}).` +
            (Number.isFinite(after) ? ` Sua Carteira vai de ${brl(walletBalance)} para ${brl(after)}.` : "") +
            (after < 0 ? "\n\nAtenção: a Carteira vai ficar negativa. O gasto já aconteceu, por isso o PigBank registra mesmo assim." : "");
        void act(btn, { message, options: { title, okText: chosen ? title : "Lançar na Carteira" }, path: "cash",
          body: { launch_id: movement.launch_id, carteira_launch_id: chosen ? chosen.id : null },
          done: chosen ? "Associado ao lançamento da Carteira." : "Registrado como pago em dinheiro." });
      } }, out);
  }
  // Fluxo conferido (só pelo detalhe do Histórico): trocar o par e desfazer.
  function matched(movement, declared, out) {
    const match = movement.match;
    out.nodes.push(element("p", "Conferida com o extrato", "modal-label"),
      element("p", `${match.description} · ${money(match.amount)} · ${match.date} · ${match.bank}`, "msub"));
    if (!movement.fluxo) return;  // caixinha/aporte: trocar e desfazer não fazem parte deste pacote
    if (movement.candidates.length) {
      txPicker(movement, { label: "Trocar a transação",
        ask: tx => `${declared}\n\nConferida hoje com: ${txText(match)}.\n\nPassa a ser: ${txText(tx)}.\n\nA transação atual volta para o extrato. Confirma a troca?`,
        send: tx => ({ launch_id: movement.launch_id, transaction_id: tx.id, replace_transaction_id: match.id }) }, out);
    }
    const undo = button("Desfazer a conferência", "btn-cancel");
    undo.addEventListener("click", () => void act(undo, {
      message: `${declared}\n\nConferida com: ${txText(match)}.\n\nAo desfazer, a transação volta para o extrato e esta declaração fica a conferir. Ela não será mais ligada automaticamente a outra transação.`,
      options: { title: "Desfazer a conferência", okText: "Desfazer", danger: true },
      path: "undo", body: { launch_id: movement.launch_id, transaction_id: match.id }, done: "Conferência desfeita." }));
    out.buttons.push(undo);
  }
  function pending(movement, declared, out) {
    out.nodes.push(element("p", "Não confirmado no extrato", "modal-label"));
    if (!movement.candidates.length) {
      out.nodes.push(element("p", "Ainda não há transação compatível. Confira o extrato do banco; sincronizar sem essa transação não confirma a declaração.", "msub"));
    } else {
      txPicker(movement, { label: movement.fluxo ? "Escolher a transação do banco" : "Transação no extrato",
        ask: tx => `${declared}\n\nExtrato: ${tx.description}, ${money(tx.amount)}, ${tx.date}, ${tx.bank}.\n\nConfirma que são a mesma movimentação já feita no banco?` +
          (movement.fluxo ? " Depois de escolher, esta declaração não será mais ligada automaticamente a outra transação." : ""),
        send: tx => ({ launch_id: movement.launch_id, transaction_id: tx.id }) }, out);
    }
    if (movement.fluxo && movement.cash) cashPicker(movement, declared, out);
    if (movement.fluxo && typeof window.confirmDeleteLaunch === "function") {
      const del = button("Apagar", "ld-del");
      del.addEventListener("click", () => {
        close();  // um overlay por vez: a confirmação do apagar abre sozinha
        void window.confirmDeleteLaunch(movement.launch_id, movement.name, Math.abs(movement.amount));
      });
      out.buttons.push(del);
    }
  }
  function item(movement) {
    const section = element("section", null, "modal-row");
    section.style.cssText = "border-top:1px solid var(--glass-border);padding-top:16px;overflow-wrap:anywhere";
    const receita = movement.tipo === "receita";
    // Despesa/receita "pelo banco" (D8): valor sem sinal e sem o texto de aporte.
    const value = movement.fluxo ? brl(Math.abs(movement.amount)) : money(movement.amount);
    const head = movement.fluxo
      ? `${receita ? "Receita" : "Gasto"} · ${value} · ${receita ? "Recebido no banco" : "Pago pelo banco"} · ${movement.date}`
      : `${value} · ${movement.bank || "Banco a conferir"} · ${movement.date}`;
    const out = { nodes: [], buttons: [] };
    const name = orBlank(movement.name);
    (movement.match ? matched : pending)(movement, `Declaração: ${name}, ${value}, ${movement.date}.`, out);
    section.append(element("h4", name), element("p", head, "msub"), ...out.nodes);
    if (out.buttons.length) section.append(row(...out.buttons));
    return section;
  }
  async function load() {
    if (!overlay) return;  // fechado com o POST em voo: a ação já valeu, só falta o afterSave
    const content = overlay.querySelector("[data-movements]");
    content.replaceChildren(element("p", "Carregando declarações…", "msub"));
    try {
      const query = launchId == null ? "" : `?launch_id=${encodeURIComponent(launchId)}`;
      const response = await fetch(`/open-finance/${activeUser}/movements${query}`, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (!overlay) return;
      const movements = data.movements || [];
      walletBalance = Number(data.wallet_balance);
      content.replaceChildren(...movements.map(item));
      if (!movements.length) content.append(element("p", launchId == null ? "Nenhuma declaração aguardando confirmação." : "Esta declaração já foi resolvida ou apagada.", "msub"));
    } catch (_) {
      if (overlay) content.replaceChildren(element("p", "Não foi possível carregar. Feche e tente novamente.", "msub"));
    }
  }
  window.pigModalKeys("bank-movements-overlay", close);
  // `opts.launchId`: só aquela declaração, mesmo conferida (entrada pelo detalhe do Histórico).
  function open(userId, onSave, opts = {}) {
    if (overlay) return;
    activeUser = userId;
    afterSave = onSave;
    launchId = opts.launchId ?? null;
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
    closeButton = button("Fechar", "btn-cancel");
    closeButton.addEventListener("click", close);
    const actions = element("div", null, "modal-acts");
    actions.append(closeButton);
    modal.append(title, element("p", "O PigBank registra o que você declarou. A confirmação exige conferir a mesma operação no extrato; o saldo do banco não é alterado aqui.", "msub"), content, actions);
    overlay.append(modal);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.body.append(overlay);
    closeButton.focus();
    void load();
  }
  window.BankMovements = { open, close };
}());
