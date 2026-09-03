/**
 * frontend/comecar.js — wizard de primeira configuração, servido em /onboarding.
 *
 * Sem framework e sem build, como o resto do frontend deste repositório. Toda a
 * lógica mora aqui: o HTML só tem marcação e `data-action`/`data-role`, e não
 * existe um único handler inline (`onclick=`) — a página inteira roda por
 * delegação de evento. Isso é de propósito: os 139 `onclick=` do dashboard.html
 * são o que ainda segura o 'unsafe-inline' no script-src da CSP, e código novo
 * não vai aumentar essa dívida.
 *
 * Servido por @router.get("/comecar.js") em frontend/routes/static_pages.py —
 * sem essa rota o arquivo dá 404 (não há StaticFiles mount neste projeto).
 *
 * Reuso, não duplicação: `window.pbCsrfHeaders` vem do auth-refresh.js (que
 * também renova o access em 401 automaticamente) e `alertModal` vem do
 * modals.js. Nada disso é reimplementado aqui.
 */
(function () {
  "use strict";

  /* ─── Constantes ──────────────────────────────────────────────────────── */

  var TOTAL_STEPS = 5;
  var STEP_WELCOME = 1, STEP_MONEY = 2, STEP_WHATSAPP = 3, STEP_REPORT = 4, STEP_DONE = 5;

  /** Espelha cards.py:69-73 — a fonte de verdade é o servidor; isto é só a UI. */
  var CARD_FLAGS = ["Visa", "Mastercard", "Elo", "Amex", "Hipercard", "Outros"];
  var CARD_COLORS = [
    ["purple", "Roxo"], ["coral", "Coral"], ["gold", "Dourado"],
    ["green", "Verde"], ["blue", "Azul"], ["gray", "Cinza"],
  ];

  /**
   * As 4 opções de resumo × os 3 booleanos de daily_report_prefs.
   *
   * O banco NÃO tem um campo de frequência: são três flags independentes
   * (schema.py:498,516,519), e as três nascem `true`. Duas consequências que o
   * resto do arquivo respeita:
   *   1. o PATCH manda SEMPRE os três juntos — mandar um só deixaria os outros
   *      ligados e a "escolha única" seria mentira;
   *   2. nenhuma opção nasce marcada, porque nenhuma das quatro reproduz o
   *      estado default (true/true/true). Pré-marcar seria disfarçar uma
   *      mudança de preferência como confirmação.
   */
  var REPORT_CHOICES = [
    { id: "diario",  label: "Todo dia",    hint: "Um resumo por dia",       daily: true,  weekly: false, monthly: false },
    { id: "semanal", label: "Toda semana", hint: "Toda segunda de manhã",   daily: false, weekly: true,  monthly: false },
    { id: "mensal",  label: "Todo mês",    hint: "No dia 1",                daily: false, weekly: false, monthly: true  },
    { id: "nenhum",  label: "Não quero",   hint: "Sem resumo automático",   daily: false, weekly: false, monthly: false },
  ];

  var WA_POLL_MS = 3000;
  var WA_POLL_LIMIT = 30; // 30 × 3s = 90s

  /* ─── Estado único do wizard ──────────────────────────────────────────── */

  var state = {
    userId: null,
    firstName: "",
    step: STEP_WELCOME,
    completed: false,
    viewed: {},            // passos que já emitiram telemetria de view
    inFlight: false,       // uma requisição de escrita por vez
    cards: [],
    cardsMax: null,
    balance: 0,
    waLinked: false,
    waCode: "",
    waLink: "",
    waPollTimer: null,
    waPollCount: 0,
    reportChoice: null,
    reportCurrent: null,
  };

  /* ─── Funções puras (testáveis sem DOM) ───────────────────────────────── */

  /** Opção da UI → o corpo do PATCH de notificações. */
  function reportPrefsFor(choiceId) {
    var choice = null;
    for (var i = 0; i < REPORT_CHOICES.length; i++) {
      if (REPORT_CHOICES[i].id === choiceId) { choice = REPORT_CHOICES[i]; break; }
    }
    if (!choice) return null;
    return {
      daily_report_enabled: choice.daily,
      weekly_report_enabled: choice.weekly,
      monthly_report_enabled: choice.monthly,
    };
  }

  /**
   * O WhatsApp está vinculado?
   *
   * NÃO usar `whatsapp_linked` do /auth/dashboard-profile: ele é
   * `bool(whatsapp_verified_at)`, e essa coluna só é escrita pelo auto-link por
   * telefone (db_support.py:926). Quem vinculou pelo código — `link 123456` →
   * link_platform_identity (db/users.py:323) — nunca ganha o carimbo, e a
   * confirmação nunca apareceria pra ele. `identities` cobre os dois caminhos.
   */
  function isWhatsAppLinked(security) {
    var list = (security && security.identities) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].provider === "whatsapp") return true;
    }
    return false;
  }

  /** Onde reabrir o wizard: o passo salvo, dentro dos limites. */
  function resumeStep(serverState) {
    var step = parseInt((serverState && serverState.step) || 0, 10);
    if (!step || step < STEP_WELCOME) return STEP_WELCOME;
    return Math.min(step, TOTAL_STEPS);
  }

  /** Já existe cartão com esse nome? Evita 409 e clique duplo. */
  function hasCardNamed(cards, name) {
    var wanted = String(name || "").trim().toLowerCase();
    if (!wanted) return false;
    for (var i = 0; i < (cards || []).length; i++) {
      if (String(cards[i].name || "").trim().toLowerCase() === wanted) return true;
    }
    return false;
  }

  /* ─── DOM helpers ─────────────────────────────────────────────────────── */

  function el(role) { return document.querySelector('[data-role="' + role + '"]'); }
  function els(role) { return Array.prototype.slice.call(document.querySelectorAll('[data-role="' + role + '"]')); }
  function stepEl(n) { return document.querySelector('.onb-step[data-step="' + n + '"]'); }
  function show(node, visible) { if (node) node.hidden = !visible; }
  function text(node, value) { if (node) node.textContent = value; }

  function showError(message) {
    var box = el("error");
    if (!box) return;
    box.textContent = message;
    box.classList.add("show");
  }
  function clearError() {
    var box = el("error");
    if (!box) return;
    box.textContent = "";
    box.classList.remove("show");
  }

  /* ─── Rede ────────────────────────────────────────────────────────────── */

  function csrf(extra) {
    return window.pbCsrfHeaders ? window.pbCsrfHeaders(extra) : (extra || {});
  }

  async function apiGet(path) {
    var res = await fetch(path, { credentials: "same-origin" });
    if (!res.ok) throw await apiError(res);
    return res.json();
  }

  async function apiSend(method, path, body) {
    var res = await fetch(path, {
      method: method,
      credentials: "same-origin",
      headers: csrf({ "Content-Type": "application/json" }),
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) throw await apiError(res);
    return res.json();
  }

  async function apiError(res) {
    var data = null;
    try { data = await res.json(); } catch (_) { /* corpo não-JSON */ }
    var detail = data && data.detail;
    var err = new Error(
      typeof detail === "string" ? detail : "Não foi possível concluir agora."
    );
    err.status = res.status;
    err.detail = detail;
    return err;
  }

  /**
   * Uma escrita por vez, e o botão fica inerte enquanto ela está em voo.
   * É o que impede clique duplo (ou retentativa impaciente) de criar dois
   * cartões ou dois lançamentos de saldo inicial.
   */
  async function withBusy(button, fn) {
    if (state.inFlight) return;
    state.inFlight = true;
    if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
    try {
      return await fn();
    } finally {
      state.inFlight = false;
      if (button) { button.disabled = false; button.removeAttribute("aria-busy"); }
    }
  }

  /* ─── Persistência do progresso ───────────────────────────────────────── */

  /** Grava passo/conclusão/telemetria. Nunca derruba a navegação. */
  async function persist(payload) {
    try {
      return await apiSend("POST", "/onboarding/state", payload);
    } catch (err) {
      // Progresso é conveniência: se falhar, o usuário continua o fluxo e no
      // pior caso recomeça de um passo anterior na próxima visita.
      return null;
    }
  }

  /* ─── Navegação ───────────────────────────────────────────────────────── */

  function renderProgress() {
    text(el("step-current"), String(state.step));
    text(el("step-total"), String(TOTAL_STEPS));
    var fill = el("progress");
    if (fill) fill.style.width = Math.round((state.step / TOTAL_STEPS) * 100) + "%";
  }

  function goTo(step) {
    state.step = Math.min(Math.max(step, STEP_WELCOME), TOTAL_STEPS);
    clearError();
    stopWaPoll();

    for (var n = 1; n <= TOTAL_STEPS; n++) show(stepEl(n), n === state.step);
    renderProgress();
    window.scrollTo(0, 0);

    // Telemetria de view só na primeira vez em cada passo — voltar e avançar de
    // novo não pode inflar a contagem do funil.
    var firstView = !state.viewed[state.step];
    state.viewed[state.step] = true;
    persist({ step: state.step, event: firstView ? "view" : null });
    loadStep(state.step);
  }

  function next() { goTo(state.step + 1); }

  function skip() {
    // O evento carrega o passo PULADO (não o seguinte) — é o número que
    // responde "onde as pessoas desistem" na query de funil. O avanço do
    // progresso vem do goTo abaixo, que persiste o passo novo.
    persist({ step: state.step, event: "skip" });
    goTo(state.step + 1);
  }

  async function finish() {
    firePixel();
    await persist({ step: TOTAL_STEPS, completed: true });
    window.location.replace("/home");
  }

  async function skipAll() {
    // Pular é uma decisão do usuário: marca concluído pra o wizard não voltar
    // a aparecer no próximo login.
    await persist({ step: state.step, completed: true });
    window.location.replace("/home");
  }

  /**
   * Conversão de ativação. Evento PRÓPRIO: `CompleteRegistration` já é
   * disparado no cadastro (cadastro.html e a CAPI server-side), e reusá-lo aqui
   * contaria a mesma pessoa duas vezes, corrompendo o sinal das campanhas.
   */
  function firePixel() {
    try {
      if (window.fbq && state.userId) {
        window.fbq("trackCustom", "OnboardingComplete", {}, { eventID: "onb_" + state.userId });
      }
      // GA4: evento próprio pelo mesmo motivo do parágrafo acima — `sign_up` já
      // saiu no cadastro, e reusar aqui contaria a mesma pessoa duas vezes.
      if (window.gtag && state.userId) {
        window.gtag("event", "onboarding_complete", { step: state.step });
      }
    } catch (_) { /* rastreio nunca pode quebrar o fluxo */ }
  }

  /* ─── Passo 2: dinheiro ───────────────────────────────────────────────── */

  async function loadMoney() {
    try {
      var setup = await apiGet("/account/" + state.userId + "/setup-status");
      state.balance = Number(setup.balance || 0);
      // Conta que já tem saldo não pode receber saldo inicial (409 no servidor):
      // some com o campo em vez de deixar o usuário bater na parede.
      show(el("balance-form"), state.balance === 0);
      show(el("balance-done"), state.balance !== 0);
    } catch (_) { /* o passo funciona sem isso */ }

    await refreshCards();
  }

  async function refreshCards() {
    try {
      var data = await apiGet("/cards/" + state.userId + "/summary");
      state.cards = data.cards || [];
    } catch (_) {
      state.cards = [];
    }
    renderCards();
  }

  function renderCards() {
    var list = el("cards-list");
    if (!list) return;
    list.innerHTML = "";
    state.cards.forEach(function (card) {
      var li = document.createElement("li");
      var name = document.createElement("strong");
      name.textContent = card.name || "Cartão";
      var meta = document.createElement("span");
      meta.textContent = "fecha " + (card.closing_day || "?") + " · vence " + (card.due_day || "?");
      li.appendChild(name);
      li.appendChild(meta);
      list.appendChild(li);
    });
  }

  function openCardForm(open) {
    show(el("card-form"), open);
    show(el("add-card-btn"), !open);
    if (open) {
      fillSelect(el("card-flag"), CARD_FLAGS.map(function (f) { return [f, f]; }));
      fillSelect(el("card-color"), CARD_COLORS);
      var nameInput = document.getElementById("onb-card-name");
      if (nameInput) nameInput.focus();
    }
  }

  function fillSelect(select, pairs) {
    if (!select || select.options.length) return;
    pairs.forEach(function (pair) {
      var opt = document.createElement("option");
      opt.value = pair[0];
      opt.textContent = pair[1];
      select.appendChild(opt);
    });
  }

  async function saveBalance(button) {
    var input = document.getElementById("onb-balance");
    var value = parseFloat(input && input.value);
    if (isNaN(value) || value < 0) {
      showError("Digite um valor válido (zero ou positivo).");
      return;
    }
    await withBusy(button, async function () {
      clearError();
      try {
        await apiSend("POST", "/account/" + state.userId + "/initial-balance", { amount: value });
        state.balance = value;
        show(el("balance-form"), false);
        show(el("balance-done"), true);
      } catch (err) {
        if (err.status === 409) {
          // Já lançou algo pelo WhatsApp antes de abrir o app: estado normal.
          show(el("balance-form"), false);
          show(el("balance-done"), true);
          return;
        }
        showError(err.message);
      }
    });
  }

  async function saveCard(button) {
    var name = (document.getElementById("onb-card-name") || {}).value || "";
    var closing = parseInt((document.getElementById("onb-card-closing") || {}).value, 10);
    var due = parseInt((document.getElementById("onb-card-due") || {}).value, 10);
    var flag = (el("card-flag") || {}).value || null;
    var color = (el("card-color") || {}).value || null;

    name = name.trim();
    if (!name) { showError("Dê um nome ao cartão."); return; }
    if (!(closing >= 1 && closing <= 31)) { showError("Dia de fechamento deve estar entre 1 e 31."); return; }
    if (!(due >= 1 && due <= 31)) { showError("Dia de vencimento deve estar entre 1 e 31."); return; }

    await withBusy(button, async function () {
      clearError();
      // Relê o estado antes de criar: além do clique duplo, cobre o caso de o
      // cartão ter sido criado em outra aba ou pelo WhatsApp no meio do wizard.
      await refreshCards();
      if (hasCardNamed(state.cards, name)) {
        showError('Você já tem um cartão chamado "' + name + '".');
        return;
      }
      try {
        await apiSend("POST", "/cards/" + state.userId, {
          name: name, closing_day: closing, due_day: due, flag: flag, color: color,
        });
        openCardForm(false);
        clearCardForm();
        await refreshCards();
      } catch (err) {
        if (err.status === 403 && err.detail && err.detail.error === "pro_required") {
          renderCardLimit();
          openCardForm(false);
          return;
        }
        showError(err.message);
      }
    });
  }

  function clearCardForm() {
    ["onb-card-name", "onb-card-closing", "onb-card-due"].forEach(function (id) {
      var node = document.getElementById(id);
      if (node) node.value = "";
    });
  }

  /**
   * Limite de cartões do plano Grátis (cards_max = 1).
   * O CTA é um <a href="/precos"> de propósito: o auth-refresh.js esconde esses
   * anchors dentro do app iOS (diretriz 3.1.1 da App Store). Um <button> com
   * location.href não seria pego pela regra e deixaria CTA de compra visível.
   */
  function renderCardLimit() {
    var box = el("cards-limit");
    if (!box) return;
    box.innerHTML = "";
    box.appendChild(document.createTextNode("Seu plano permite um cartão. "));
    var link = document.createElement("a");
    link.href = "/precos";
    link.textContent = "Ver planos";
    box.appendChild(link);
    box.appendChild(document.createTextNode(" pra cadastrar mais."));
    show(box, true);
    show(el("add-card-btn"), false);
  }

  /* ─── Passo 3: WhatsApp ───────────────────────────────────────────────── */

  async function loadWhatsApp() {
    var linked = false;
    try {
      var security = await apiGet("/settings/" + state.userId + "/security");
      linked = isWhatsAppLinked(security);
    } catch (_) { /* segue pro caminho de vincular */ }

    state.waLinked = linked;
    show(el("wa-linked"), linked);
    show(el("wa-pending"), !linked);
    if (linked) return;

    // Um código por entrada no passo: /auth/link-code tem rate limit de 15/hora,
    // e o poll bate no /settings/{id}/security, que não tem limite.
    if (!state.waCode) {
      try {
        var data = await apiSend("POST", "/auth/link-code", {});
        state.waCode = data.link_code || "";
        state.waLink = data.whatsapp_link || "";
      } catch (err) {
        showError(err.message);
        return;
      }
    }
    renderWaCode();
    startWaPoll();
  }

  function renderWaCode() {
    // O bot só vincula com "link 123456" (wa_runtime.py:389) e o deeplink de
    // hoje leva texto fixo "Olá" — por isso a mensagem inteira aparece aqui,
    // pronta pra copiar, em vez de só o número.
    text(el("wa-code"), state.waCode ? "link " + state.waCode : "—");
    var link = el("wa-link");
    if (link && state.waLink) {
      link.href = state.waLink;
      show(link, true);
    }
  }

  function startWaPoll() {
    stopWaPoll();
    state.waPollCount = 0;
    state.waPollTimer = window.setInterval(async function () {
      state.waPollCount += 1;
      if (state.waPollCount > WA_POLL_LIMIT) {
        stopWaPoll();
        text(el("wa-status"), "Não recebi ainda. Mande a mensagem e toque em Continuar.");
        return;
      }
      try {
        var security = await apiGet("/settings/" + state.userId + "/security");
        if (isWhatsAppLinked(security)) {
          stopWaPoll();
          state.waLinked = true;
          show(el("wa-pending"), false);
          show(el("wa-linked"), true);
        }
      } catch (_) { /* tenta de novo no próximo tick */ }
    }, WA_POLL_MS);
  }

  function stopWaPoll() {
    if (state.waPollTimer) {
      window.clearInterval(state.waPollTimer);
      state.waPollTimer = null;
    }
  }

  /* ─── Passo 4: resumo ─────────────────────────────────────────────────── */

  async function loadReport() {
    try {
      state.reportCurrent = await apiGet("/settings/" + state.userId + "/notifications");
    } catch (_) {
      state.reportCurrent = null;
    }
    renderReportCurrent();
    renderReportChoices();
    fillHourSelect();
  }

  function renderReportCurrent() {
    var box = el("report-current");
    if (!box || !state.reportCurrent) return;
    var on = [];
    if (state.reportCurrent.daily_report_enabled) on.push("diário");
    if (state.reportCurrent.weekly_report_enabled) on.push("semanal");
    if (state.reportCurrent.monthly_report_enabled) on.push("mensal");
    box.textContent = on.length
      ? "Hoje você recebe: " + on.join(", ") + "."
      : "Hoje você não recebe resumo automático.";
  }

  /**
   * Nenhuma opção nasce marcada e o botão nasce desabilitado: o estado default
   * do banco (os três ligados) não corresponde a nenhuma das quatro, então
   * pré-marcar seria mudar a preferência do usuário fingindo confirmá-la.
   */
  function renderReportChoices() {
    var wrap = el("report-choices");
    if (!wrap || wrap.children.length) return;
    REPORT_CHOICES.forEach(function (choice) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "onb-choice";
      button.setAttribute("role", "radio");
      button.setAttribute("aria-checked", "false");
      button.setAttribute("data-action", "pick-report");
      button.setAttribute("data-choice", choice.id);
      var label = document.createElement("strong");
      label.textContent = choice.label;
      var hint = document.createElement("small");
      hint.textContent = choice.hint;
      button.appendChild(label);
      button.appendChild(hint);
      wrap.appendChild(button);
    });
  }

  function fillHourSelect() {
    var select = el("report-hour");
    if (!select || select.options.length) return;
    for (var h = 0; h < 24; h++) {
      var opt = document.createElement("option");
      opt.value = String(h);
      opt.textContent = (h < 10 ? "0" + h : h) + ":00";
      select.appendChild(opt);
    }
    var current = state.reportCurrent && state.reportCurrent.daily_report_hour;
    select.value = String(current == null ? 9 : current);
  }

  function pickReport(choiceId) {
    state.reportChoice = choiceId;
    els("report-choices").forEach(function (wrap) {
      Array.prototype.slice.call(wrap.children).forEach(function (node) {
        node.setAttribute("aria-checked", node.getAttribute("data-choice") === choiceId ? "true" : "false");
      });
    });
    show(el("report-hour-wrap"), choiceId === "diario");
    var save = el("save-report");
    if (save) save.disabled = false;
  }

  async function saveReport(button) {
    var prefs = reportPrefsFor(state.reportChoice);
    if (!prefs) { showError("Escolha uma frequência."); return; }
    await withBusy(button, async function () {
      clearError();
      var body = {
        daily_report_enabled: prefs.daily_report_enabled,
        weekly_report_enabled: prefs.weekly_report_enabled,
        monthly_report_enabled: prefs.monthly_report_enabled,
      };
      if (prefs.daily_report_enabled) {
        var hour = parseInt((el("report-hour") || {}).value, 10);
        if (!isNaN(hour)) { body.daily_report_hour = hour; body.daily_report_minute = 0; }
      }
      try {
        await apiSend("PATCH", "/settings/" + state.userId + "/notifications", body);
        next();
      } catch (err) {
        showError(err.message);
      }
    });
  }

  /* ─── Carregamento por passo ──────────────────────────────────────────── */

  function loadStep(step) {
    if (!state.userId) return;
    if (step === STEP_MONEY) loadMoney();
    else if (step === STEP_WHATSAPP) loadWhatsApp();
    else if (step === STEP_REPORT) loadReport();
  }

  /* ─── Delegação de eventos (zero onclick inline) ──────────────────────── */

  var ACTIONS = {
    next: function () { next(); },
    skip: function () { skip(); },
    "skip-all": function () { skipAll(); },
    finish: function () { finish(); },
    "save-balance": function (button) { saveBalance(button); },
    "add-card": function () { openCardForm(true); },
    "cancel-card": function () { openCardForm(false); },
    "save-card": function (button) { saveCard(button); },
    "pick-report": function (button) { pickReport(button.getAttribute("data-choice")); },
    "save-report": function (button) { saveReport(button); },
  };

  function onClick(event) {
    var target = event.target.closest ? event.target.closest("[data-action]") : null;
    if (!target) return;
    var handler = ACTIONS[target.getAttribute("data-action")];
    if (!handler) return;
    event.preventDefault();
    handler(target);
  }

  /* ─── Boot ────────────────────────────────────────────────────────────── */

  async function boot() {
    // Guarda: sem a marcação do wizard não há o que iniciar. É o que deixa os
    // helpers puros acima serem carregados isoladamente (vm, sem DOM) nos
    // testes sem disparar nenhuma chamada de rede.
    if (!document.querySelector(".onb-card")) return;

    // Boot uma vez só. Se o arquivo for avaliado duas vezes (script incluído em
    // duplicidade, ou re-execução de init como a que o pb-nav.js faz nas páginas
    // que ele converte), cada avaliação registraria OUTRO listener de clique — e
    // aí um toque em "Continuar" avançaria dois passos de uma vez. Medido: com
    // três avaliações, um clique pulou do passo 1 pro 4.
    if (window.__pbOnboardingBooted) return;
    window.__pbOnboardingBooted = true;

    document.addEventListener("click", onClick);

    var profile = null, server = null;
    try {
      profile = await apiGet("/auth/dashboard-profile");
    } catch (err) {
      // Sem sessão válida a página não tem o que fazer.
      window.location.replace("/login?next=%2Fonboarding");
      return;
    }
    state.userId = profile.user_id;
    var name = (profile.display_name || "").trim();
    state.firstName = name ? name.split(/\s+/)[0] : "";
    if (state.firstName) {
      els("greet-name").forEach(function (n) { n.textContent = ", " + state.firstName; });
      els("greet-name-end").forEach(function (n) { n.textContent = ", " + state.firstName; });
    }

    try {
      server = await apiGet("/onboarding/state");
    } catch (_) { /* começa do zero */ }

    // Retomada: volta no passo salvo em vez de recomeçar. `?step=` só é aceito
    // pra voltar do /settings (conectar banco) — e nunca além do que já foi
    // alcançado, pra o link não virar um pulo do fluxo.
    var target = resumeStep(server);
    var asked = parseInt(new URLSearchParams(window.location.search).get("step"), 10);
    if (asked >= STEP_WELCOME && asked <= target) target = asked;

    goTo(target);
  }

  /* Helpers puros expostos pros testes (tests/frontend/*.test.mjs), que rodam
     o arquivo em vm sem DOM e nunca disparam o DOMContentLoaded abaixo. */
  window.PBOnboarding = {
    TOTAL_STEPS: TOTAL_STEPS,
    REPORT_CHOICES: REPORT_CHOICES,
    reportPrefsFor: reportPrefsFor,
    isWhatsAppLinked: isWhatsAppLinked,
    resumeStep: resumeStep,
    hasCardNamed: hasCardNamed,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
