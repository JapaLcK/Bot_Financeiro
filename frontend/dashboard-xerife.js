/* global API, USER_ID, csrfHeaders, readResponsePayload, esc, fmt, loadAgentesView */
/* Configuração e esperados do Xerife. O dialog nativo cuida de foco, Escape e isolamento modal. */
(function () {
  const dialog = document.getElementById("xerife-dialog");
  if (!dialog) return;
  const status = document.getElementById("xerife-status");
  const content = document.getElementById("xerife-content");
  const retry = document.getElementById("xerife-retry");
  const configForm = document.getElementById("xerife-config-form");
  const ruleForm = document.getElementById("xerife-rule-form");
  const newRule = document.getElementById("xerife-new-rule");
  let offset = 0, pendingOffset = 0, generation = 0, busy = false, launches = [];
  const field = (form, name) => form.elements.namedItem(name);
  const endpoint = () => `${API}/agents/${USER_ID}/xerife`;
  const date = value => String(value || "").slice(0, 10).split("-").reverse().join("/");

  async function request(path, method = "GET", body) {
    const res = await fetch(endpoint() + path, {
      method, credentials: "same-origin",
      ...(method === "GET" ? {} : { headers: csrfHeaders({ "Content-Type": "application/json" }) }),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const data = await readResponsePayload(res);
    if (!res.ok) {
      if (res.status === 403) throw new Error("Seu plano não permite configurar o Xerife agora.");
      if (res.status === 422) throw new Error("Confira os campos: use valores dentro dos limites e uma descrição e categoria válidas.");
      throw new Error(typeof data.detail === "string" ? data.detail : "Não deu para concluir. Tente novamente.");
    }
    return data;
  }

  function renderLists(data) {
    launches = data.lancamentos || [];
    document.getElementById("xerife-esperados-list").innerHTML = launches.map(l => `
      <div class="xerife-item"><div><strong>${esc(l.descricao || l.categoria)}</strong>
        <p>${esc(l.categoria)} · ${esc(fmt(l.valor))} · ${esc(date(l.criado_em))}</p></div>
        <div class="xerife-actions"><button type="button" class="btn-cancel" data-xerife-undo="${Number(l.id)}" aria-label="Desfazer esperado: ${esc(l.descricao || l.categoria)}">Desfazer</button>
        <button type="button" class="btn-cancel" data-xerife-rule-from="${Number(l.id)}">Criar regra</button></div></div>`).join("") || "<p>Nenhum gasto marcado nesta página.</p>";
    document.getElementById("xerife-regras-list").innerHTML = (data.regras || []).map(r => `
      <div class="xerife-item"><div><strong>${esc(r.descricao)}</strong>
        <p>${esc(r.categoria)} · até ${esc(fmt(r.teto))} · ${r.data_fim ? `gastos até ${esc(date(r.data_fim))}` : "sem data final"}</p></div>
        <button type="button" class="btn-cancel" data-xerife-delete="${esc(r.id)}" aria-label="Desfazer regra: ${esc(r.descricao)}">Desfazer regra</button></div>`).join("") || "<p>Nenhuma regra recorrente.</p>";
    dialog.querySelector('[data-xerife-page="prev"]').hidden = offset === 0;
    dialog.querySelector('[data-xerife-page="next"]').hidden = !data.has_more;
  }

  async function load({ fillConfig = false, message = "", pageOffset = offset } = {}) {
    const current = ++generation;
    pendingOffset = pageOffset;
    status.textContent = "Carregando…";
    retry.hidden = true;
    try {
      const data = await request(`/esperados?limit=50&offset=${pageOffset}`);
      if (current !== generation || !dialog.open) return;
      offset = pageOffset;
      if (fillConfig) {
        const cfg = data.config;
        field(configForm, "multiplicador").value = cfg.multiplicador;
        field(configForm, "minimo").value = cfg.minimo;
        field(configForm, "email_enabled").checked = cfg.email_enabled;
      }
      renderLists(data);
      content.hidden = false;
      status.textContent = message;
    } catch (err) {
      if (current !== generation || !dialog.open) return;
      status.textContent = err.message || "Não foi possível carregar. Tente novamente.";
      retry.hidden = false;
    }
  }

  async function mutate(path, method, body, message) {
    if (busy) return false;
    busy = true;
    const controls = [...dialog.querySelectorAll("button, input, summary")];
    controls.forEach(el => { el.disabled = true; });
    status.textContent = "Salvando…";
    try {
      await request(path, method, body);
      // Keep typed config until explicitly saved; list actions must not discard a draft.
      await load({ message });
      await loadAgentesView(true);
      return true;
    } catch (err) {
      status.textContent = err.message || "Não deu para salvar. Tente novamente.";
      return false;
    } finally {
      busy = false;
      controls.forEach(el => { el.disabled = false; });
      // A removed row must not leave keyboard focus on the document body.
      if (!dialog.contains(document.activeElement)) dialog.querySelector("[data-xerife-close]").focus();
    }
  }

  document.addEventListener("click", e => {
    const open = e.target.closest("[data-xerife-config]");
    if (!open) return;
    content.hidden = true;
    offset = 0;
    ruleForm.reset();
    newRule.open = false;
    dialog.showModal();
    load({ fillConfig: true });
  });
  dialog.addEventListener("cancel", e => { if (busy) e.preventDefault(); });
  dialog.addEventListener("close", () => {
    generation++;
    document.querySelector("[data-xerife-config]")?.focus();
  });
  retry.addEventListener("click", () => load({ fillConfig: content.hidden, pageOffset: pendingOffset }));

  dialog.addEventListener("click", async e => {
    const btn = e.target.closest("button");
    if (!btn || busy) return;
    if (btn.hasAttribute("data-xerife-close")) return dialog.close();
    if (btn.dataset.xerifePage) {
      return load({ pageOffset: Math.max(0, offset + (btn.dataset.xerifePage === "next" ? 50 : -50)) });
    }
    if (btn.dataset.xerifeUndo) return mutate(`/lancamentos/${btn.dataset.xerifeUndo}/esperado`, "PUT", { esperado: false }, "Marcação desfeita. Regras recorrentes ainda podem se aplicar; alertas antigos não serão reenviados.");
    if (btn.dataset.xerifeDelete) return mutate(`/regras/${encodeURIComponent(btn.dataset.xerifeDelete)}`, "DELETE", undefined, "Regra desfeita. Alertas antigos não serão reenviados.");
    if (btn.dataset.xerifeRuleFrom) {
      const launch = launches.find(l => l.id === Number(btn.dataset.xerifeRuleFrom));
      if (!launch) return;
      field(ruleForm, "descricao").value = launch.descricao || "";
      field(ruleForm, "categoria").value = launch.categoria || "";
      field(ruleForm, "teto").value = launch.valor;
      field(ruleForm, "data_fim").value = "";
      newRule.open = true;
      field(ruleForm, "descricao").focus();
    }
  });
  configForm.addEventListener("submit", e => {
    e.preventDefault();
    mutate("/config", "PATCH", {
      multiplicador: Number(field(configForm, "multiplicador").value),
      minimo: Number(field(configForm, "minimo").value),
      email_enabled: field(configForm, "email_enabled").checked,
    }, "Configurações salvas. Elas valem para as próximas avaliações.");
  });
  ruleForm.addEventListener("submit", async e => {
    e.preventDefault();
    const saved = await mutate("/regras", "POST", {
      descricao: field(ruleForm, "descricao").value.trim(),
      categoria: field(ruleForm, "categoria").value.trim(),
      teto: Number(field(ruleForm, "teto").value),
      data_fim: field(ruleForm, "data_fim").value || null,
    }, "Regra salva. Gastos correspondentes ficam fora da média e dos alertas de anomalia.");
    if (saved) { ruleForm.reset(); newRule.open = false; }
  });
})();
