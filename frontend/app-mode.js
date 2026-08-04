/**
 * app-mode.js — Ativa o "modo app" do PigBank e monta a tab bar inferior.
 *
 * Ativação (qualquer um):
 *   - user agent contém "PigBankApp" (WebView do app iOS/Capacitor)
 *   - ?pbapp=1 na URL (persiste em localStorage — preview no navegador)
 *   - localStorage.pbApp === "1"
 *   Desativação no preview: ?pbapp=0
 *
 * Deve ser incluído no <head> (sem defer) pra classe pb-app existir antes do
 * primeiro paint — evita o "pulo" de layout do chrome de site sumindo.
 */
(() => {
  const qs = new URLSearchParams(location.search);
  if (qs.get("pbapp") === "0") { try { localStorage.removeItem("pbApp"); } catch (_) {} }
  if (qs.get("pbapp") === "1") { try { localStorage.setItem("pbApp", "1"); } catch (_) {} }

  let stored = null;
  try { stored = localStorage.getItem("pbApp"); } catch (_) {}
  const inApp = /PigBankApp/.test(navigator.userAgent) || stored === "1";
  if (!inApp) return;

  const root = document.documentElement;
  root.classList.add("pb-app");

  // Página atual → classe no body (CSS escopa por página) + aba ativa
  const PAGES = {
    "/home":         "home",
    "/app":          "app",
    "/comandos-app": "comandos",
    "/settings":     "settings",
  };
  // Variantes do preview local (mock serve arquivos .html e o dash na raiz)
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    Object.assign(PAGES, {
      "/home.html": "home", "/dashboard.html": "app",
      "/comandos-app.html": "comandos", "/settings.html": "settings",
      "/": "app",
    });
  }
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const page = PAGES[path];

  // Safe areas exigem viewport-fit=cover (WebKit aplica dinamicamente)
  function fixViewport() {
    const m = document.querySelector('meta[name="viewport"]');
    if (m && !/viewport-fit/.test(m.content)) m.content += ", viewport-fit=cover";
  }

  const TABS = [
    { href: "/home", label: "Início", icon:
      '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/></svg>' },
    { href: "/app", label: "Dashboard", icon:
      '<svg viewBox="0 0 24 24" width="24" height="24"><rect x="3" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.5"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.5"/></svg>' },
    { href: "/comandos-app", label: "O que pedir", icon:
      '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.3 8.9 8.9 0 0 1-3.7-.8L3 20l1.1-4.1a8 8 0 0 1-1.1-4.4A8.4 8.4 0 0 1 11.5 3.2 8.4 8.4 0 0 1 21 11.5z"/></svg>' },
    { href: "/settings", label: "Ajustes", icon:
      '<svg viewBox="0 0 24 24" width="24" height="24"><circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3h.1a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5h.1a1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8v.1a1.6 1.6 0 0 0 1.5 1h.1a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/></svg>' },
  ];

  // Lançamento: no dashboard abre o modal direto; nas outras páginas navega
  // pro dashboard com ?lancar=1 (o init lá embaixo dispara o modal).
  function fabLaunch(ev) {
    ev.preventDefault();
    if (typeof window.openLaunchModal === "function") { window.openLaunchModal(); return; }
    location.href = "/app?lancar=1";
  }

  function buildTabbar() {
    if (!page) { root.classList.add("pb-no-tabs"); return; }
    document.body.classList.add("pb-page-" + page);

    const bar = document.createElement("nav");
    bar.className = "pb-tabbar";
    bar.setAttribute("aria-label", "Navegação principal");
    const tabHtml = t => {
      const active = PAGES[t.href] === page;
      return `<a class="pb-tab${active ? " active" : ""}" href="${t.href}"` +
        `${active ? ' aria-current="page"' : ""}>${t.icon}<span>${t.label}</span></a>`;
    };
    const fabHtml =
      '<a class="pb-tab pb-tab-fab" href="/app?lancar=1" aria-label="Lançar"><span>' +
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" ' +
      'stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></span></a>';
    bar.innerHTML =
      tabHtml(TABS[0]) + tabHtml(TABS[1]) + fabHtml + tabHtml(TABS[2]) + tabHtml(TABS[3]);
    bar.querySelector(".pb-tab-fab").addEventListener("click", fabLaunch);
    document.body.appendChild(bar);
  }

  // Glifos de texto (☰, 🐷) viram SVG/imagem — WebView pode não ter as fontes
  function hardenGlyphs() {
    const burger =
      '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    document.querySelectorAll(".sidenav-toggle").forEach(el => {
      el.innerHTML = burger;
    });
    // Botão da conta: ícone de pessoa (era um segundo hambúrguer, confundia)
    document.querySelectorAll(".hamburger-icon").forEach(el => {
      el.innerHTML =
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>';
    });
    const fabIcon = document.querySelector("#piggy-fab span[aria-hidden]");
    if (fabIcon) {
      fabIcon.innerHTML =
        '<img src="/brand/avatar.webp" alt="" style="width:40px;height:40px;' +
        'border-radius:50%;display:block;object-fit:cover" />';
    }
  }

  // ── Visão geral estilo app: seção "Próximos vencimentos" + cabeçalho de
  // "Últimos lançamentos" com Ver todos. Injetados FORA do #grid (que o
  // render() do dashboard reescreve a cada snapshot).
  function enhanceOverview() {
    if (page !== "app") return;
    const ov = document.getElementById("overview-view");
    const launchesWrap = document.getElementById("launches-wrap");
    if (!ov || !launchesWrap) return;

    const bills = document.createElement("div");
    bills.id = "pb-bills-sec";
    bills.style.display = "none";
    ov.insertBefore(bills, document.getElementById("launches-title") || launchesWrap);

    const sec = document.createElement("div");
    sec.className = "pb-sec";
    sec.innerHTML = '<b>Últimos lançamentos</b><a href="#" id="pb-see-all">Ver todos</a>';
    ov.insertBefore(sec, launchesWrap);
    sec.querySelector("#pb-see-all").addEventListener("click", ev => {
      ev.preventDefault();
      document.body.classList.add("pb-launches-all");
      ev.target.remove();
    });

    loadUpcomingBills(bills);
  }

  async function loadUpcomingBills(el) {
    // USER_ID do dashboard é let (não vaza pro window) — valida por conta própria
    let uid = window.USER_ID || 0;
    if (!uid) {
      try {
        const rv = await fetch("/auth/validate", { credentials: "same-origin" });
        if (!rv.ok) return;
        uid = (await rv.json()).user_id || 0;
      } catch (_) { return; }
    }
    if (!uid) return;
    let bills = [];
    try {
      const r = await fetch(`/recurring-bills/${uid}`, { credentials: "same-origin" });
      if (!r.ok) return; // sem acesso a contas → seção não aparece
      bills = ((await r.json()).bills || []).filter(b => b.status === "pending").slice(0, 3);
    } catch (_) { return; }
    if (!bills.length) return;

    const fmtBRL = v => Number(v || 0).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
    const receipt =
      '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M4 2v20l2-1.5L8 22l2-1.5L12 22l2-1.5L16 22l2-1.5L20 22V2l-2 1.5L16 2l-2 1.5L12 2l-2 1.5L8 2 6 3.5 4 2Z"/><path d="M8 7h8M8 11h8M8 15h5"/></svg>';
    const dueLabel = due => {
      const d = Math.round((new Date(due + "T00:00:00") - new Date().setHours(0, 0, 0, 0)) / 864e5);
      if (d < 0)   return `<small class="late">Venceu há ${-d} dia${d === -1 ? "" : "s"}</small>`;
      if (d === 0) return '<small class="late">Vence hoje</small>';
      if (d === 1) return "<small>Vence amanhã</small>";
      return `<small>Vence em ${d} dias</small>`;
    };
    const esc = s => String(s || "").replace(/[<>&"]/g, c => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" }[c]));

    el.innerHTML =
      '<div class="pb-sec"><b>Próximos vencimentos</b><a href="#" id="pb-bills-all">Ver todos</a></div>' +
      '<div class="pb-bills">' +
      bills.map(b =>
        `<div class="pb-bill"><div class="pb-bill-ico">${receipt}</div>` +
        `<div class="pb-bill-name"><b>${esc(b.name)}</b><small>${esc(b.category || "Conta")}</small></div>` +
        `<div class="pb-bill-val"><b>${b.amount != null ? fmtBRL(b.amount) : "R$ —"}</b>${dueLabel(b.due_date)}</div></div>`
      ).join("") +
      "</div>";
    el.style.display = "";
    el.querySelector("#pb-bills-all").addEventListener("click", ev => {
      ev.preventDefault();
      if (typeof window.navigateTo === "function") window.navigateTo("fixed");
    });
  }

  // Chegou pelo + de outra página: abre o modal de lançamento assim que o
  // dashboard.js terminar de definir a função (poll curto).
  function maybeOpenLaunch() {
    if (page !== "app" || qs.get("lancar") !== "1") return;
    let tries = 0;
    const t = setInterval(() => {
      if (typeof window.openLaunchModal === "function") {
        clearInterval(t);
        window.openLaunchModal();
      } else if (++tries > 40) {
        clearInterval(t);
      }
    }, 150);
  }

  // ── Settings vira "Minha conta": header com avatar + banner de segurança
  function enhanceSettings() {
    if (page !== "settings") return;
    const ph = document.querySelector(".page-header");
    if (!ph || document.querySelector(".pb-acct-head")) return;

    const head = document.createElement("div");
    head.className = "pb-acct-head";
    head.innerHTML =
      '<div><h1>Minha conta</h1><p>Gerencie sua conta e segurança</p></div>' +
      '<img src="/brand/avatar.webp" alt="" />';
    ph.parentNode.insertBefore(head, ph);

    const ban = document.createElement("div");
    ban.className = "pb-protect";
    ban.innerHTML =
      '<svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" ' +
      'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 2 4 5.5v5.2c0 5 3.4 9.2 8 10.3 4.6-1.1 8-5.3 8-10.3V5.5L12 2z"/>' +
      '<rect x="9" y="10" width="6" height="5" rx="1"/><path d="M10 10V8.5a2 2 0 0 1 4 0V10"/></svg>' +
      '<div class="pb-protect-txt"><b>Proteja sua conta</b>' +
      "<small>Mantenha seus dados seguros e seu acesso sempre protegido.</small></div>" +
      "<button type=\"button\">Revisar segurança</button>";
    head.after(ban);
    ban.querySelector("button").addEventListener("click", () => {
      if (typeof window.showSettingsSection === "function") window.showSettingsSection("security");
      const sb = document.querySelector(".sidebar");
      if (sb) sb.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    const lbl = document.querySelector(".sidebar-section");
    if (lbl) lbl.textContent = "Segurança e acesso";
  }

  function init() { fixViewport(); buildTabbar(); hardenGlyphs(); enhanceOverview(); enhanceSettings(); maybeOpenLaunch(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
