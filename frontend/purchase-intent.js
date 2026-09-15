/**
 * Continuidade da compra entre Planos, autenticação, checkout e onboarding.
 *
 * Guarda somente três escolhas não sensíveis: plano, ciclo e meio de pagamento.
 * CPF/CNPJ, cartão, token de checkout e identificadores do provedor nunca entram
 * aqui. sessionStorage mantém a intenção apenas nesta aba e a validade curta
 * evita retomar uma escolha esquecida muito tempo depois.
 */
(function () {
  "use strict";

  const ACTIVE_KEY = "pb_purchase_intent_v1";
  const COMPLETE_KEY = "pb_purchase_complete_v1";
  const MAX_AGE_MS = 2 * 60 * 60 * 1000;
  const PLANS = ["essencial", "plus", "pro"];
  const CYCLES = ["monthly", "annual"];
  const METHODS = ["card", "pix"];
  const STATUSES = ["selected", "awaiting_auth", "checkout_started", "completed"];
  const PLAN_NAMES = { essencial: "Essencial", plus: "Plus", pro: "Pro" };

  function storageGet(key) {
    try { return window.sessionStorage.getItem(key); } catch (_) { return null; }
  }

  function storageSet(key, value) {
    try { window.sessionStorage.setItem(key, JSON.stringify(value)); return true; }
    catch (_) { return false; }
  }

  function storageRemove(key) {
    try { window.sessionStorage.removeItem(key); } catch (_) {}
  }

  function valid(raw, completed) {
    if (!raw || raw.version !== 1) return null;
    if (!PLANS.includes(raw.plan) || !CYCLES.includes(raw.cycle)) return null;
    if (!METHODS.includes(raw.method) || !STATUSES.includes(raw.status)) return null;
    if (raw.method === "pix" && raw.cycle !== "annual") return null;
    const timestamp = Number(completed ? raw.completedAt : raw.createdAt);
    if (!Number.isFinite(timestamp) || Date.now() - timestamp > MAX_AGE_MS || timestamp > Date.now() + 60000) return null;
    return raw;
  }

  function readKey(key, completed) {
    const raw = storageGet(key);
    if (!raw) return null;
    try {
      const intent = valid(JSON.parse(raw), completed);
      if (intent) return intent;
    } catch (_) {}
    storageRemove(key);
    return null;
  }

  function read() { return readKey(ACTIVE_KEY, false); }
  function readCompleted() { return readKey(COMPLETE_KEY, true); }

  function begin(plan, cycle, method) {
    const intent = valid({
      version: 1,
      plan: plan,
      cycle: cycle,
      method: method,
      status: "selected",
      createdAt: Date.now(),
    }, false);
    if (!intent) return null;
    storageSet(ACTIVE_KEY, intent);
    storageRemove(COMPLETE_KEY);
    return intent;
  }

  function setStatus(status) {
    const intent = read();
    if (!intent || !STATUSES.includes(status) || status === "completed") return null;
    const updated = Object.assign({}, intent, { status: status });
    storageSet(ACTIVE_KEY, updated);
    return updated;
  }

  function pending() {
    const intent = read();
    return intent && intent.status === "awaiting_auth" ? intent : null;
  }

  function complete() {
    const intent = read();
    if (!intent || intent.status !== "checkout_started") return null;
    const done = Object.assign({}, intent, { status: "completed", completedAt: Date.now() });
    storageSet(COMPLETE_KEY, done);
    storageRemove(ACTIVE_KEY);
    return done;
  }

  function continueUrl() { return "/precos?compra=continuar"; }
  function authUrl() { return "/cadastro?compra=1"; }
  function afterAuth(fallback) { return pending() ? continueUrl() : (fallback || "/home"); }

  function summary(intent) {
    return PLAN_NAMES[intent.plan] + " · "
      + (intent.cycle === "annual" ? "Anual" : "Mensal") + " · "
      + (intent.method === "pix" ? "Pix" : "Cartão");
  }

  function intentBox(intent, detail) {
    const box = document.createElement("div");
    box.className = "purchase-intent";
    box.setAttribute("role", "status");
    const icon = document.createElement("span");
    icon.className = "purchase-intent__icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "✓";
    const text = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = summary(intent);
    const copy = document.createElement("span");
    copy.textContent = detail;
    text.append(title, copy);
    box.append(icon, text);
    return box;
  }

  function mountAuth() {
    const intent = pending();
    if (!intent || !document.querySelector(".auth-card")) return;
    document.querySelectorAll(".auth-card .auth-sub").forEach(function (sub) {
      if (sub.nextElementSibling && sub.nextElementSibling.classList.contains("purchase-intent")) return;
      sub.after(intentBox(intent,
        "Sua escolha está guardada. Depois de confirmar a conta, você segue direto para o pagamento."));
    });
    ["btn-login", "btn-mfa"].forEach(function (id) {
      const button = document.getElementById(id);
      if (button) button.textContent = id === "btn-mfa" ? "Verificar e continuar" : "Entrar e continuar";
    });
    const verify = document.getElementById("btn-verify");
    if (verify) verify.textContent = "Confirmar e continuar";
    const completeButton = document.getElementById("submit-btn");
    if (completeButton) completeButton.textContent = "Criar minha conta e continuar";

    document.querySelectorAll('a[href="/login"]').forEach(function (link) {
      link.href = "/login?next=" + encodeURIComponent(continueUrl());
    });
    document.querySelectorAll('a[href="/cadastro"]').forEach(function (link) {
      link.href = authUrl();
    });
    document.querySelectorAll('a[href="/auth/google/start"]').forEach(function (link) {
      link.href = "/auth/google/start?next=" + encodeURIComponent(continueUrl());
    });
  }

  function mountOnboarding() {
    const intent = readCompleted();
    const firstStep = document.querySelector('[data-step="1"]');
    if (!intent || !firstStep || firstStep.querySelector(".purchase-intent")) return;
    const actions = firstStep.querySelector(".onb-acts");
    const detail = intent.method === "pix"
      ? "Pagamento confirmado. Agora vamos deixar sua conta pronta."
      : "Período grátis ativado. Agora vamos deixar sua conta pronta.";
    firstStep.insertBefore(intentBox(intent, detail), actions || null);
  }

  window.PBPurchaseIntent = {
    begin: begin,
    read: read,
    pending: pending,
    markAwaitingAuth: function () { return setStatus("awaiting_auth"); },
    markCheckoutStarted: function () { return setStatus("checkout_started"); },
    complete: complete,
    readCompleted: readCompleted,
    clear: function () { storageRemove(ACTIVE_KEY); },
    clearCompleted: function () { storageRemove(COMPLETE_KEY); },
    continueUrl: continueUrl,
    authUrl: authUrl,
    afterAuth: afterAuth,
    summary: summary,
    mountAuth: mountAuth,
    mountOnboarding: mountOnboarding,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      mountAuth();
      mountOnboarding();
    }, { once: true });
  } else {
    mountAuth();
    mountOnboarding();
  }
})();
