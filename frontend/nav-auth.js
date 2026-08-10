// Nav ciente de login (páginas de marketing).
// - Logado: o canto direito do nav vira o MENU DA CONTA (mesmo visual do app
//   PWA — /home): header com e-mail + badge do plano e os atalhos
//   Início / Dashboard / Conectar WhatsApp / Configurações / Assinatura / Sair.
//   Os CTAs do corpo (hero/seções) continuam apontando pro dashboard.
// - Mobile: o nav quebra em duas linhas — logo + entrar/conta em cima e os
//   MESMOS botões do site (Funcionalidades / Como funciona / Planos / WhatsApp)
//   numa segunda linha, sempre visíveis (antes sumiam no celular).
// NÃO redireciona — só ajusta o nav pra não parecer deslogado.
// Estilos injetados via <style> (prefixo pb-) pra não depender do cache do site.css.
(function () {
  // Paywall (/precos?ativar=1): conta criada, ainda SEM assinatura. O menu da
  // conta APARECE (é exatamente onde o "quero sair" acontece); só os CTAs do
  // corpo ficam quietos — ali os botões são os planos.
  var isPaywall =
    location.pathname.replace(/\/+$/, "") === "/precos" &&
    new URLSearchParams(location.search).get("ativar") === "1";

  function getCsrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function csrfHeaders(extra) {
    var c = getCsrf();
    var h = extra || {};
    if (c) h["x-csrf-token"] = c;
    return h;
  }

  function doLogout() {
    fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    })
      .catch(function () {})
      .finally(function () {
        try { localStorage.setItem("finbot_logout_at", String(Date.now())); } catch (e) {}
        location.reload(); // CTAs voltam ao padrão deslogado
      });
  }

  // "Conectar WhatsApp": gera o link e abre a conversa (mesmo fluxo do app).
  // Sem depender de modals.js — em erro, cai pra página /whatsapp.
  function connectWhatsApp(ev) {
    if (ev) ev.preventDefault();
    fetch("/auth/link-code", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && data.whatsapp_link) {
          var isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
          if (isMobile) location.href = data.whatsapp_link;
          else window.open(data.whatsapp_link, "_blank", "noopener");
        } else {
          location.href = "/whatsapp";
        }
      })
      .catch(function () { location.href = "/whatsapp"; });
  }

  // Traduz o valor CRU do banco pro tier vendido ao cliente antes de exibir:
  // 'pro' é alias legado do Plus (R$ 19,90) e 'pro_max' é o Pro novo — ver
  // _STORED_PLAN_TO_TIER em core/services/plan_service.py. Sem isso o badge
  // mostraria "PLANO PRO" pra um assinante Plus e "PLANO PRO_MAX" pro Pro.
  var PLAN_ALIASES = { pro: "plus", pro_max: "pro" };
  function planLabel(plan) {
    var v = (plan || "free").trim().toLowerCase();
    v = PLAN_ALIASES[v] || v;
    return v === "free" ? "PLANO FREE" : "PLANO " + v.toUpperCase();
  }
  function shortAccount(email) {
    if (!email) return "Minha conta";
    var name = email.split("@")[0];
    return name.length > 18 ? name.slice(0, 16) + "..." : name;
  }

  // Atalhos do usuário logado (mesma lista do app PWA).
  var ACCOUNT_LINKS = [
    { icon: "🏠", label: "Início", href: "/home" },
    { icon: "📊", label: "Dashboard", href: "/app" },
    { icon: "💬", label: "Conectar WhatsApp", action: connectWhatsApp },
    { icon: "⚙️", label: "Configurações", href: "/settings?view=security" },
    { icon: "💳", label: "Gerenciar assinatura", href: "/conta" },
    { icon: "↪", label: "Sair", action: function (e) { if (e) e.preventDefault(); doLogout(); }, danger: true },
  ];

  function injectStyles() {
    if (document.getElementById("pb-nav-css")) return;
    var s = document.createElement("style");
    s.id = "pb-nav-css";
    s.textContent = [
      /* ── Menu da conta (desktop + mobile linha 1) ── */
      ".pb-acct{position:relative}",
      ".pb-acct-btn{display:flex;align-items:center;gap:9px;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);color:#fff;padding:9px 15px;border-radius:999px;font-size:.9rem;font-weight:600;cursor:pointer;font-family:inherit}",
      ".pb-acct-btn:hover{background:rgba(255,255,255,.12)}",
      ".pb-acct-dot{width:8px;height:8px;border-radius:50%;background:#C6F11A;box-shadow:0 0 0 2px rgba(198,241,26,.18),0 0 8px rgba(198,241,26,.36);flex-shrink:0}",
      ".pb-acct-lbl{max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".pb-acct-caret{font-size:.7rem;opacity:.7}",
      ".pb-acct-dd{position:absolute;right:0;top:calc(100% + 10px);width:288px;display:none;padding:8px;background:rgba(23,23,26,.98);border:1px solid rgba(255,255,255,.12);border-radius:14px;box-shadow:0 18px 44px rgba(0,0,0,.5);-webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);z-index:99}",
      ".pb-acct-dd.open{display:block}",
      ".pb-acct-head{padding:10px 12px 12px;border-bottom:1px solid rgba(255,255,255,.08);margin-bottom:6px}",
      ".pb-acct-email{font-size:.8rem;font-weight:700;color:#fff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}",
      ".pb-acct-plan{display:inline-block;margin-top:6px;padding:2px 8px;background:rgba(198,241,26,.15);color:#C6F11A;border-radius:6px;font-size:.65rem;font-weight:700;letter-spacing:.04em}",
      ".pb-acct-link{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:10px;font-size:.84rem;font-weight:600;color:rgba(255,255,255,.66);text-decoration:none;cursor:pointer;border:0;background:transparent;width:100%;text-align:left;font-family:inherit}",
      ".pb-acct-link:hover{background:rgba(255,255,255,.08);color:#fff}",
      ".pb-acct-link.danger{color:#fecaca}",
      ".pb-acct-link.danger:hover{background:rgba(248,113,113,.15);color:#fff1f2}",
      ".pb-acct-ico{width:18px;text-align:center;flex-shrink:0}",
      /* ── Nav mobile: os 4 links vão pra uma 2ª linha, sempre visíveis ── */
      "@media (max-width:900px){",
      ".nav{flex-wrap:wrap;row-gap:0}",
      ".nav .nav-logo{order:1}",
      ".nav .nav-right{order:2;margin-left:auto}",
      ".nav .nav-links{order:3;display:flex;flex-wrap:wrap;width:100%;justify-content:center;align-items:center;gap:8px 20px;margin:10px 0 0;padding-top:11px;border-top:1px solid rgba(255,255,255,.08)}",
      ".nav .nav-links a{font-size:.9rem;white-space:nowrap}",
      "}",
    ].join("");
    document.head.appendChild(s);
  }

  // ── Dropdown de conta (canto direito) ────────────────────────────────────
  function renderAccountMenu(nr) {
    var linksHtml = ACCOUNT_LINKS.map(function (l, i) {
      var cls = "pb-acct-link" + (l.danger ? " danger" : "");
      var tag = l.href ? "a" : "button";
      var attr = l.href ? 'href="' + l.href + '"' : 'type="button"';
      return "<" + tag + ' class="' + cls + '" data-acct-i="' + i + '" ' + attr + ">" +
        '<span class="pb-acct-ico">' + l.icon + "</span>" + l.label + "</" + tag + ">";
    }).join("");

    nr.innerHTML =
      '<div class="pb-acct" id="pb-acct">' +
      '<button class="pb-acct-btn" id="pb-acct-btn" type="button" aria-haspopup="menu" aria-expanded="false">' +
      '<span class="pb-acct-dot"></span>' +
      '<span class="pb-acct-lbl" id="pb-acct-lbl">Minha conta</span>' +
      '<span class="pb-acct-caret">▾</span></button>' +
      '<div class="pb-acct-dd" id="pb-acct-dd" role="menu">' +
      '<div class="pb-acct-head"><div class="pb-acct-email" id="pb-acct-email">Minha conta</div>' +
      '<span class="pb-acct-plan" id="pb-acct-plan" style="display:none"></span></div>' +
      linksHtml + "</div></div>";

    var btn = document.getElementById("pb-acct-btn");
    var dd = document.getElementById("pb-acct-dd");
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = dd.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("click", function () {
      dd.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    });
    ACCOUNT_LINKS.forEach(function (l, i) {
      if (!l.action) return;
      var el = nr.querySelector('[data-acct-i="' + i + '"]');
      if (el) el.addEventListener("click", l.action);
    });
  }

  // Preenche e-mail + plano no dropdown (mesma fonte do app).
  function loadProfile() {
    fetch("/auth/dashboard-profile", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        var email = data.email || "";
        setText("pb-acct-lbl", data.display_name || shortAccount(email));
        setText("pb-acct-email", email || "Minha conta");
        setPlan("pb-acct-plan", planLabel(data.plan || "free"));
      })
      .catch(function () {});
  }
  function setText(id, v) { var el = document.getElementById(id); if (el) el.textContent = v; }
  function setPlan(id, v) { var el = document.getElementById(id); if (el) { el.textContent = v; el.style.display = "inline-block"; } }

  injectStyles();

  fetch("/auth/validate", { credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) return; // deslogado: mantém "Entrar / Começar agora" padrão
      // 1) Nav direita: "Entrar / Começar agora" → menu da conta (com logout).
      var nr = document.querySelector(".nav .nav-right");
      if (nr) renderAccountMenu(nr);
      // 2) E-mail + plano no dropdown.
      loadProfile();
      if (isPaywall) return; // no paywall, os CTAs do corpo são os planos — não mexe
      // 3) CTAs de cadastro no corpo (hero, seções finais) → dashboard.
      //    Um CTA pode declarar destino/rótulo próprios pra logado via
      //    data-app-href / data-app-text (ex.: /agents manda pra aba de
      //    agentes do painel). Sem eles, cai no padrão "/app".
      document.querySelectorAll('a[href="/cadastro"]').forEach(function (a) {
        a.setAttribute("href", a.getAttribute("data-app-href") || "/app");
        a.textContent = a.getAttribute("data-app-text") || "Ir para o dashboard";
      });
    })
    .catch(function () {
      /* offline/erro: mantém CTAs padrão */
    });
})();
