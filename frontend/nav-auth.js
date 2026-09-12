// Nav ciente de login (páginas de marketing).
// - Logado: o canto direito do nav vira o MENU DA CONTA (mesmo visual do app
//   PWA — /home): header com e-mail + badge do plano e os atalhos
//   Início / Dashboard / Conectar WhatsApp / Configurações / Assinatura / Sair.
//   Os CTAs do corpo (hero/seções) continuam apontando pro dashboard.
// - Mobile: os MESMOS botões do site (Funcionalidades / Como funciona / Planos
//   / WhatsApp) recolhem atrás de um botão, ao lado do entrar/conta. Antes
//   ocupavam uma 2ª linha sempre aberta, e a nav é fixa: comia tela o tempo todo.
// NÃO redireciona — só ajusta o nav pra não parecer deslogado.
// Estilos injetados via <style> (prefixo pb-) pra não depender do cache do site.css.
(function () {
  // Paywall (/precos?ativar=1 ou ?escolha=1): conta criada, ainda SEM
  // assinatura — ou cortada no fim do Grátis. O menu da conta APARECE (é
  // exatamente onde o "quero sair" acontece); só os CTAs do corpo ficam
  // quietos — ali os botões são os planos.
  //
  // Os DOIS marcadores, e não só o novo. Medido 2026-09-11
  // (`grep -rn 'precos?\(ativar\|escolha\)=1' frontend/`): sobrou UM redirect
  // vivo com `ativar=1`, `home.html:1549` — o de `settings.html` saiu neste PR,
  // e a linha que ainda casa o grep lá é o comentário que registra a remoção.
  // Um bloqueado que chegue pelo redirect da home veria os CTAs de marketing na
  // página onde ele deveria assinar, então a tolerância dupla fica. Ela também
  // cobre link velho em cache e aba aberta antes do deploy, que nenhum grep vê.
  const qs = new URLSearchParams(location.search);
  const isPaywall =
    location.pathname.replace(/\/+$/, "") === "/precos" &&
    (qs.get("ativar") === "1" || qs.get("escolha") === "1");

  function getCsrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }
  function csrfHeaders(extra) {
    const c = getCsrf();
    const h = extra || {};
    if (c) h["x-csrf-token"] = c;
    return h;
  }

  /**
   * Apaga o Cache Storage deste dispositivo no logout.
   *
   * Gêmeo do `_limpaCacheNoLogout` de `auth-refresh.js`. As 12 páginas
   * públicas carregam ESTE arquivo e NÃO carregam o auth-refresh, então o
   * logout do menu de conta delas não passa pelo interceptor de fetch de lá —
   * sem esta cópia, sair pela landing deixava o cache privado intacto (Codex,
   * #170). `tests/frontend/sw_cache_privado.test.mjs` compara os dois (§0.7).
   *
   * Da PÁGINA, não por `postMessage` ao worker: o aparelho que ainda tem cache
   * privado é o controlado por um worker antigo, que não escuta `message`.
   */
  // Gêmeo do `_PRESERVA` de `auth-refresh.js` — preferência do APARELHO fica,
  // qualquer coisa derivada da CONTA sai. Lista do que preservar e não do que
  // apagar, para falhar fechado: chave nova derivada de conta é apagada por
  // default. `tests/frontend/sw_cache_privado.test.mjs` compara as duas (§0.7).
  const PRESERVA = ["pigbank_theme", "pigbank_hide_balance", "pbFabPos", "pbDebug", "pbSpa", "finbot_logout_at", "finbot_reset_at"];

  // Recebe o NOME, não o objeto: `window.localStorage` é um getter que LANÇA
  // com dados do site bloqueados, e a avaliação do argumento ficava fora do
  // `try`. Aqui o dano era maior que no auth-refresh — o `.finally` do
  // `doLogout` rejeitava e o `location.reload()` nunca rodava, então nas
  // páginas públicas o "Sair" não fazia nada visível.
  function apagaStorage(nome) {
    try {
      const store = window[nome];
      Object.keys(store).forEach(function (k) {
        if (PRESERVA.indexOf(k) === -1) store.removeItem(k);
      });
    } catch (_e) { /* storage bloqueado (Safari privado): nada a apagar */ }
  }

  // Gêmeo do `_desregistraWorkers` de `auth-refresh.js`. Desregistra ANTES de
  // apagar: um worker antigo ainda no controle tem `cache.put` assíncrono e uma
  // request em voo noutra aba podia recriar o cache depois do delete.
  function desregistraWorkers() {
    try {
      const sw = navigator.serviceWorker;
      if (!sw || !sw.getRegistrations) return Promise.resolve();
      return sw.getRegistrations()
        .then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); })
        .catch(function () {});
    } catch (_e) { /* sem service worker: nada a desregistrar */ }
    return Promise.resolve();
  }

  function limpaCacheNoLogout() {
    apagaStorage("localStorage");
    apagaStorage("sessionStorage");
    return desregistraWorkers()
      .then(function () {
        if (!window.caches) return;
        return caches.keys().then(function (ks) {
          return Promise.all(ks.map(function (k) { return caches.delete(k); }));
        });
      })
      .catch(function () { /* sem SW ou sem CacheStorage: nada a limpar */ });
  }

  function doLogout() {
    fetch("/auth/logout", {
      method: "POST",
      credentials: "same-origin",
      headers: csrfHeaders(),
    })
      .catch(function () {})
      // A limpeza é AGUARDADA antes do reload: navegação descarta o documento,
      // e uma limpeza disparada e esquecida não tem garantia de terminar — o
      // cache privado sobreviveria ao logout justamente no aparelho
      // compartilhado (Codex, #170).
      .finally(function () {
        return limpaCacheNoLogout().then(function () {
          try { localStorage.setItem("finbot_logout_at", String(Date.now())); } catch (_e) {}
          location.reload(); // CTAs voltam ao padrão deslogado
        });
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
          const isMobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent || "");
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
  const PLAN_ALIASES = { pro: "plus", pro_max: "pro" };
  function planLabel(plan) {
    let v = (plan || "free").trim().toLowerCase();
    v = PLAN_ALIASES[v] || v;
    return v === "free" ? "PLANO FREE" : "PLANO " + v.toUpperCase();
  }
  function shortAccount(email) {
    if (!email) return "Minha conta";
    const name = email.split("@")[0];
    return name.length > 18 ? name.slice(0, 16) + "..." : name;
  }

  // Atalhos do usuário logado (mesma lista do app PWA).
  const ACCOUNT_LINKS = [
    { icon: '<i class="ph ph-house" aria-hidden="true"></i>', label: "Início", href: "/home" },
    { icon: '<i class="ph ph-chart-bar" aria-hidden="true"></i>', label: "Dashboard", href: "/app" },
    { icon: '<i class="ph ph-whatsapp-logo" aria-hidden="true"></i>', label: "Conectar WhatsApp", action: connectWhatsApp },
    { icon: '<i class="ph ph-gear" aria-hidden="true"></i>', label: "Configurações", href: "/settings?view=security" },
    { icon: '<i class="ph ph-credit-card" aria-hidden="true"></i>', label: "Gerenciar assinatura", href: "/conta" },
    { icon: "↪", label: "Sair", action: function (e) { if (e) e.preventDefault(); doLogout(); }, danger: true },
  ];

  function injectStyles() {
    if (document.getElementById("pb-nav-css")) return;
    const s = document.createElement("style");
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
      /* ── Nav mobile: menu recolhido ───────────────────────────────────
         Os links ficavam SEMPRE abertos numa 2ª linha, e a nav é sticky (não
         fixed): o bloco acompanhava a rolagem comendo a tela. Agora recolhem. */
      ".pb-burger{display:none}",
      "@media (max-width:900px){",
      /* UM auto por linha, o resto por justify-content: dois `margin-left:auto` na
         mesma linha flex REPARTEM a sobra em vez de empurrar para a direita, e o
         "Entrar / Criar conta" descolava da borda. A sobra da 1ª linha vai para o
         logo; a 2ª, quando o botão quebra, alinha pelo container. O column-gap de
         6px nasceu para salvar 375px com o CTA LONGO e já não é o que segura essa
         largura. Os números que moravam aqui envelheceram duas vezes, então em vez de outro número, o comando (console, na largura que interessa): n=document.querySelector('.nav'),c=getComputedStyle(n),i=[...n.children].filter(e=>e.getBoundingClientRect().width>0),{pede:i.reduce((s,e)=>s+e.getBoundingClientRect().width,0)+parseFloat(c.columnGap)*(i.length-1),ha:n.clientWidth-parseFloat(c.paddingLeft)-parseFloat(c.paddingRight)} */
      ".nav{flex-wrap:wrap;row-gap:0;column-gap:6px;justify-content:flex-end}",
      ".nav .nav-logo{order:1;margin-right:auto}",
      ".nav .nav-right{order:2;margin-left:0}",
      /* alvo de toque 44×44 (mínimo iOS/WCAG) mesmo com o glifo pequeno */
      ".pb-burger{order:3;display:flex;align-items:center;justify-content:center;width:44px;height:44px;margin-left:0;padding:0;flex-shrink:0;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);border-radius:12px;color:#fff;font-size:1.1rem;line-height:1;cursor:pointer;font-family:inherit}",
      ".pb-burger:hover{background:rgba(255,255,255,.12)}",
      ".nav .nav-links{order:4;display:none;width:100%;flex-direction:column;align-items:stretch;gap:2px;margin:10px 0 0;padding-top:10px;border-top:1px solid rgba(255,255,255,.08)}",
      ".nav.pb-nav-open .nav-links{display:flex}",
      /* linha de 44px: item de menu, não link solto no meio da barra */
      ".nav .nav-links a{font-size:.95rem;white-space:nowrap;padding:11px 12px;border-radius:10px;min-height:44px;display:flex;align-items:center}",
      ".nav .nav-links a:hover{background:rgba(255,255,255,.06)}",
      "}",
    ].join("");
    document.head.appendChild(s);
  }

  // ── Dropdown de conta (canto direito) ────────────────────────────────────
  function renderAccountMenu(nr) {
    const linksHtml = ACCOUNT_LINKS.map(function (l, i) {
      const cls = "pb-acct-link" + (l.danger ? " danger" : "");
      const tag = l.href ? "a" : "button";
      const attr = l.href ? 'href="' + l.href + '"' : 'type="button"';
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

    const btn = document.getElementById("pb-acct-btn");
    const dd = document.getElementById("pb-acct-dd");
    btn.addEventListener("click", function () {
      const open = dd.classList.toggle("open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    // Esta guarda É o `stopPropagation` que ficava no toggle acima, movido para
    // cá: lá ele barrava também o listener do burger e os dois menus abriam
    // juntos — e tirá-lo sem pôr a guarda abre e fecha o dropdown no MESMO clique.
    document.addEventListener("click", function (e) {
      if (e.target.closest(".pb-acct-btn")) return;  // do BOTÃO: .pb-acct-link ainda fecha
      dd.classList.remove("open");
      btn.setAttribute("aria-expanded", "false");
    });
    ACCOUNT_LINKS.forEach(function (l, i) {
      if (!l.action) return;
      const el = nr.querySelector('[data-acct-i="' + i + '"]');
      if (el) el.addEventListener("click", l.action);
    });
  }

  // Preenche e-mail + plano no dropdown (mesma fonte do app).
  function loadProfile() {
    fetch("/auth/dashboard-profile", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        const email = data.email || "";
        setText("pb-acct-lbl", data.display_name || shortAccount(email));
        setText("pb-acct-email", email || "Minha conta");
        setPlan("pb-acct-plan", planLabel(data.plan || "free"));
      })
      .catch(function () {});
  }
  function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
  function setPlan(id, v) { const el = document.getElementById(id); if (el) { el.textContent = v; el.style.display = "inline-block"; } }

  // ── Botão do menu mobile ──────────────────────────────────────────────────
  // O CSS acima recolhe .nav-links abaixo de 900px; sem este botão não haveria
  // como reabrir. Quem mostra ou esconde o botão é o CSS; o JS só zera o estado
  // ao SAIR do breakpoint, senão pb-nav-open e aria-expanded="true" ficariam de
  // pé num botão invisível e quem gira o aparelho reencontraria o menu aberto.
  //
  // O botão entra ANTES do .nav-links de propósito: um DOM só serve a UMA das
  // duas ordens visuais (desktop logo→links→entrar, mobile logo→entrar→botão→
  // links), então uma delas sempre diverge do Tab. Esta posição mantém o
  // desktop igual ao visual e deixa o Tab entrar no menu ABERTO logo depois do
  // botão; o desvio sobra no mobile fechado. Pô-lo depois do .nav-right faria o
  // contrário e deixaria os links inalcançáveis por Tab com o menu aberto —
  // nada vem depois deles. Não mova sem medir os dois estados.
  function mountBurger() {
    const nav = document.querySelector(".nav");
    const links = nav && nav.querySelector(".nav-links");
    if (!nav || !links || nav.querySelector(".pb-burger")) return;

    if (!links.id) links.id = "pb-nav-links";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pb-burger";
    b.setAttribute("aria-label", "Abrir menu");
    b.setAttribute("aria-expanded", "false");
    b.setAttribute("aria-controls", links.id);
    b.innerHTML = "&#9776;";

    function setOpen(open) {
      nav.classList.toggle("pb-nav-open", open);
      b.setAttribute("aria-expanded", open ? "true" : "false");
      b.setAttribute("aria-label", open ? "Fechar menu" : "Abrir menu");
    }
    b.addEventListener("click", function () {
      setOpen(!nav.classList.contains("pb-nav-open"));
    });
    // Escape fecha e devolve o foco ao botão — senão o foco fica órfão no menu.
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && nav.classList.contains("pb-nav-open")) {
        setOpen(false);
        b.focus();
      }
    });
    document.addEventListener("click", function (e) {
      if (!e.target.closest(".pb-burger,.nav-links")) setOpen(false);  // fecha em TODO clique fora do gatilho e de dentro do menu — categoria MAIS LARGA que o `nav.contains` que substituiu, não só "o menu da conta também fecha": logo, `.nav-right` e o padding da barra passaram a fechar (medido; nas 12 páginas eles navegam, então o estado não chega a ser visto). `closest` pede target Element, e o `nav.contains` era total: `document.dispatchEvent(new MouseEvent("click"))` estoura (1 erro deslogado, 2 logado) e o setOpen não roda — sem emissor no frontend/, fica sem guarda (§0.2)
    });
    // Clicar num link fecha; em âncora a página não troca e o foco cairia no body.
    // Quem conserta é o `tabindex`: medido no Chromium, a navegação para o
    // fragmento foca o alvo sozinha quando ele vira focável — o `focus()` abaixo é
    // inerte aqui, e fica para motor onde isso não é garantido (NÃO medido).
    links.addEventListener("click", function (e) {
      const a = e.target.closest("a"), alvo = a && a.hash && document.getElementById(a.hash.slice(1));
      if (a) setOpen(false);
      if (alvo) { alvo.setAttribute("tabindex", "-1"); alvo.focus({ preventScroll: true }); }  // o tabindex fica no DOM pelo resto da sessão (as 2 seções da index, depois de ativadas): não muda ordem de Tab nem pinta anel fora do foco, mas é mutação permanente
    });
    // Passou de 900px: o botão some e o menu volta a ser a barra do desktop.
    const mq = window.matchMedia("(max-width:900px)");
    const onBreakpoint = e => { if (!e.matches) setOpen(false); };
    if (mq.addEventListener) mq.addEventListener("change", onBreakpoint);
    else if (mq.addListener) mq.addListener(onBreakpoint); // Safari < 14

    nav.insertBefore(b, links);
  }

  injectStyles();
  mountBurger();

  fetch("/auth/validate", { credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) return; // deslogado: mantém "Entrar / Criar conta" padrão
      // 1) Nav direita: "Entrar / Criar conta" → menu da conta (com logout).
      const nr = document.querySelector(".nav .nav-right");
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
