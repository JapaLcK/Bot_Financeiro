(function () {
  "use strict";

  const data = window.CRMDemoData;
  const views = window.CRMDemoViews;
  const root = document.getElementById("crm-content");
  const orgSelect = document.getElementById("organization-select");
  const search = document.getElementById("crm-search");
  const results = document.getElementById("search-results");
  const state = { orgId: data.organizations[0].id, conversationId: null, customGoal: null, customGoals: {} };

  function routeName() {
    const name = location.hash.slice(1);
    return views.routes[name] ? name : "overview";
  }

  function updateCounts() {
    const inOrg = (items) => items.filter((item) => item.org === state.orgId);
    document.getElementById("pipeline-badge").textContent = inOrg(data.deals).filter((item) => item.stage !== "won").length;
    document.getElementById("tasks-badge").textContent = inOrg(data.tasks).filter((item) => !item.done).length;
    document.getElementById("inbox-badge").textContent = inOrg(data.conversations).reduce((sum, item) => sum + item.unread, 0);
  }

  function render() {
    const route = routeName();
    const current = views.routes[route];
    state.customGoal = state.customGoals[state.orgId] || null;
    results.hidden = true;
    document.title = `${current.title} · PigBank CRM`;
    document.getElementById("route-title").textContent = current.title;
    document.getElementById("route-context").textContent = current.context;
    document.querySelectorAll("[data-route]").forEach((link) => {
      const active = link.dataset.route === route;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    root.innerHTML = current.render(state);
    updateCounts();
    closeSidebar();
    root.focus({ preventScroll: true });
  }

  function toast(message, type = "info") {
    const region = document.getElementById("toast-region");
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.innerHTML = `<i class="ph ${type === "success" ? "ph-check-circle" : "ph-info"}"></i><span>${views.safe(message)}</span>`;
    region.append(item);
    window.setTimeout(() => item.remove(), 3600);
  }

  function openDialog(id) {
    const dialog = document.getElementById(id);
    if (dialog && !dialog.open) dialog.showModal();
  }

  function closeSidebar() {
    document.body.classList.remove("sidebar-open");
    document.querySelector(".mobile-menu").setAttribute("aria-expanded", "false");
  }

  function conversation(id) {
    return data.conversations.find((item) => item.id === id && item.org === state.orgId);
  }

  function moveDeal(button) {
    const deal = data.deals.find((item) => item.id === button.dataset.id);
    if (!deal) return;
    const current = data.stages.findIndex((stage) => stage.id === deal.stage);
    const next = data.stages[current + Number(button.dataset.dir)];
    if (!next) return;
    deal.stage = next.id;
    render();
    toast(`${deal.company} avançou para ${next.label}.`, "success");
  }

  function toggleWhatsapp(id) {
    const instance = data.whatsapp.find((item) => item.id === id);
    if (!instance) return;
    instance.status = instance.status === "connected" ? "paused" : "connected";
    instance.quality = instance.status === "connected" ? "Alta" : "Atenção";
    render();
    toast(`${instance.name}: ${instance.status === "connected" ? "conexão simulada ativa" : "instância pausada"}.`, "success");
  }

  function connectWhatsapp() {
    const disconnected = data.whatsapp.find((item) => item.org === state.orgId && item.status === "disconnected");
    if (disconnected) {
      disconnected.status = "connected";
      disconnected.quality = "Alta";
    } else {
      data.whatsapp.push({ id: `w${Date.now()}`, org: state.orgId, name: "Nova instância oficial", type: "API Oficial", number: "+55 11 3000-2026", status: "connected", quality: "Alta", today: 0 });
    }
    render();
    toast("Instância conectada em modo de demonstração.", "success");
  }

  function handleAction(target) {
    const action = target.closest("[data-action]");
    if (!action) return;
    const name = action.dataset.action;
    if (name === "open-sidebar") {
      document.body.classList.add("sidebar-open");
      action.setAttribute("aria-expanded", "true");
    } else if (name === "close-sidebar") closeSidebar();
    else if (name === "collapse-sidebar") {
      const collapsed = document.body.classList.toggle("sidebar-collapsed");
      action.setAttribute("aria-expanded", String(!collapsed));
      action.querySelector("span").textContent = collapsed ? "Expandir menu" : "Recolher menu";
    } else if (name === "open-profile") location.hash = "profile";
    else if (name === "open-contact") {
      const item = data.conversations.find((entry) => entry.org === state.orgId && entry.contact === action.dataset.contact);
      state.conversationId = item ? item.id : null;
      location.hash = "inbox";
      if (routeName() === "inbox") render();
    }
    else if (name === "move-deal") moveDeal(action);
    else if (name === "select-conversation") { state.conversationId = action.dataset.id; render(); }
    else if (name === "assign-conversation") {
      const item = conversation(action.dataset.id);
      if (item) { item.owner = "Lucas Martins"; item.unread = 0; render(); toast("Conversa atribuída a você.", "success"); }
    } else if (name === "close-conversation" || name === "reopen-conversation") {
      const item = conversation(action.dataset.id);
      if (item) { item.status = name === "close-conversation" ? "closed" : "open"; render(); toast(item.status === "closed" ? "Conversa encerrada." : "Conversa reaberta.", "success"); }
    } else if (name === "toggle-whatsapp") toggleWhatsapp(action.dataset.id);
    else if (name === "connect-whatsapp") connectWhatsapp();
    else if (name === "open-invite") openDialog("invite-dialog");
    else if (name === "open-goal") openDialog("goal-dialog");
    else if (name === "open-upgrade") {
      const org = data.organizations.find((item) => item.id === state.orgId);
      document.getElementById("current-plan-label").textContent = org.plan;
      openDialog("upgrade-dialog");
    } else if (name === "close-upgrade") document.getElementById("upgrade-dialog").close();
    else if (name === "confirm-upgrade") {
      const org = data.organizations.find((item) => item.id === state.orgId);
      org.plan = "Enterprise";
      orgSelect.querySelector(`option[value="${state.orgId}"]`).textContent = `${org.name} · ${org.plan}`;
      document.getElementById("upgrade-dialog").close();
      render(); toast("Upgrade simulado concluído. Nenhuma cobrança foi feita.", "success");
    } else if (name === "notifications") toast("3 alertas: uma tarefa crítica e duas novas mensagens.");
    else if (name === "quick-create") toast("Ação rápida demonstrativa: use Funil, Equipe ou Metas para concluir um fluxo.");
    else if (name === "secondary-demo") toast(action.dataset.message || "Controle disponível na versão completa do produto.");
  }

  function showSearch(value) {
    const query = value.trim().toLocaleLowerCase("pt-BR");
    if (!query) { results.hidden = true; return; }
    const pools = [
      ...data.companies.filter((item) => item.org === state.orgId).map((item) => ({ title: item.name, meta: item.segment, route: "companies" })),
      ...data.contacts.filter((item) => item.org === state.orgId).map((item) => ({ title: item.name, meta: item.company, route: "contacts" })),
      ...data.deals.filter((item) => item.org === state.orgId).map((item) => ({ title: item.title, meta: item.company, route: "pipeline" }))
    ];
    const found = pools.filter((item) => `${item.title} ${item.meta}`.toLocaleLowerCase("pt-BR").includes(query)).slice(0, 6);
    results.innerHTML = found.length ? found.map((item) => `<a href="#${item.route}"><i class="ph ${item.route === "contacts" ? "ph-user" : item.route === "pipeline" ? "ph-handshake" : "ph-buildings"}"></i><span><strong>${views.safe(item.title)}</strong><small>${views.safe(item.meta)}</small></span><i class="ph ph-caret-right"></i></a>`).join("") : `<p>Nenhum resultado em ${views.safe(data.organizations.find((item) => item.id === state.orgId).name)}.</p>`;
    results.hidden = false;
  }

  function handleForms(event) {
    if (event.submitter && event.submitter.value === "cancel") return;
    if (event.target.id === "invite-form") {
      event.preventDefault();
      const form = new FormData(event.target);
      document.getElementById("invite-dialog").close();
      event.target.reset();
      toast(`Convite enviado para ${form.get("email")} como ${form.get("role")}.`, "success");
    } else if (event.target.id === "goal-form") {
      event.preventDefault();
      const form = new FormData(event.target);
      state.customGoals[state.orgId] = { title: form.get("title"), scope: form.get("scope"), target: Number(form.get("target")) };
      document.getElementById("goal-dialog").close();
      event.target.reset();
      render(); toast("Meta criada e adicionada à jornada da equipe.", "success");
    } else if (event.target.id === "profile-form") {
      event.preventDefault();
      toast("Perfil atualizado nesta demonstração.", "success");
    }
  }

  function initialize() {
    orgSelect.innerHTML = data.organizations.map((org) => `<option value="${org.id}">${views.safe(org.name)} · ${org.plan}</option>`).join("");
    orgSelect.addEventListener("change", () => {
      state.orgId = orgSelect.value; state.conversationId = null;
      render(); toast(`Organização alterada para ${data.organizations.find((item) => item.id === state.orgId).name}.`, "success");
    });
    document.addEventListener("click", (event) => {
      handleAction(event.target);
      if (!event.target.closest(".global-search")) results.hidden = true;
    });
    document.addEventListener("change", (event) => {
      const action = event.target.dataset.action;
      if (action === "toggle-task") {
        const task = data.tasks.find((item) => item.id === event.target.dataset.id);
        if (task) { task.done = event.target.checked; render(); toast(task.done ? "Tarefa concluída. +40 pontos de consistência." : "Tarefa reaberta.", task.done ? "success" : "info"); }
      } else if (action === "change-role") {
        const member = data.team.find((item) => item.id === event.target.dataset.id);
        if (member) { member.role = event.target.value; toast(`Permissão de ${member.name} atualizada para ${member.role}.`, "success"); }
      }
    });
    document.addEventListener("submit", handleForms);
    search.addEventListener("input", () => showSearch(search.value));
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { search.value = ""; results.hidden = true; }
      if (event.key === "Enter") { const first = results.querySelector("a"); if (first) first.click(); }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); search.focus(); }
      if (event.key === "Escape") closeSidebar();
    });
    window.addEventListener("hashchange", render);
    if (!location.hash) history.replaceState(null, "", "#overview");
    render();
  }

  initialize();
}());
