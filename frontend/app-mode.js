/**
 * app-mode.js — Ativa o "modo app" do PigBank e monta a tab bar inferior.
 *
 * Ativação — dois gatilhos, os dois derivados do AMBIENTE:
 *   - user agent contém "PigBankApp" (o app iOS/Capacitor manda PigBankApp/1.0)
 *   - ícone instalado / PWA (display-mode: standalone, ou navigator.standalone
 *     no iOS)
 *
 * Não existe atalho de navegador. O preview ?pbapp=1 foi REMOVIDO: nada que
 * o usuário toque — link, query, storage — liga o modo app. O preview antigo
 * gravava localStorage.pbApp pra sempre e sem UI de saída, e travou o Safari
 * de um usuário de verdade; qualquer link velho (histórico, autocomplete,
 * bookmark, WhatsApp) reabria a armadilha. Quem já tinha ficado preso se
 * destrava sozinho no primeiro load desta versão — porque o gate abaixo parou
 * de LER a chave, não porque alguém a apague. Único resíduo: o removeItem, que
 * é higiene.
 *
 * QA que precisa ver o layout de app: abra no app de verdade, ou falsifique o
 * user agent no devtools (Safari: Desenvolvedor → User Agent; Chrome: Network
 * conditions) — spoof vale só naquela sessão do inspetor e não grava nada no
 * aparelho.
 *
 * Deve ser incluído no <head> (sem defer) pra classe pb-app existir antes do
 * primeiro paint — evita o "pulo" de layout do chrome de site sumindo.
 */
(() => {
  // Higiene, não destravamento. Quem visitou ?pbapp=1 antes desta versão já
  // volta ao site no primeiro load, e o mérito é do GATE: ele não lê mais
  // storage nenhum pra decidir. Esta linha só apaga uma chave legada que
  // ninguém consulta.
  // O try/catch, esse sim, é obrigatório: é a ÚNICA linha do arquivo que toca
  // storage, e com storage bloqueado (Safari privado, cookies off) o acesso
  // ESTOURA. Sem o catch a exceção mataria o script antes do classList.add e o
  // app nativo abriria com layout de site (nav, footer, sem tab bar).
  try { localStorage.removeItem("pbApp"); } catch (_) {}

  // PWA instalada roda o mesmo "modo app" do app iOS — atualizações do site
  // chegam nas duas cascas sem passo extra.
  const standalone =
    (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) ||
    window.navigator.standalone === true;
  const inApp = /PigBankApp/.test(navigator.userAgent) || standalone;
  if (!inApp) return;

  const root = document.documentElement;
  root.classList.add("pb-app");

  // PWA aberta na landing (start_url antiga "/"): entra pelo mesmo caminho do
  // app iOS — /login pula direto pro /home quando a sessão está viva.
  if (standalone && location.pathname === "/") {
    location.replace("/login");
    return;
  }

  // Página atual → classe no body (CSS escopa por página) + aba ativa
  const PAGES = {
    "/home":         "home",
    "/app":          "app",
    "/comandos-app": "comandos",
    "/changelog":    "changelog",
    "/settings":     "settings",
  };
  // Variantes do servidor local de dev (mock serve .html e o dash na raiz)
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    Object.assign(PAGES, {
      "/home.html": "home", "/dashboard.html": "app",
      "/comandos-app.html": "comandos", "/changelog.html": "changelog",
      "/settings.html": "settings", "/": "app",
    });
  }
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const page = PAGES[path];
  // A página VIVA: igual a `page` no load, atualizada pelo pb-nav a cada troca
  // client-side. Callback assíncrono que decide "aba ativa" (o /auth/me do
  // syncNewsTab) tem que olhar pra cá — o const de boot fica velho depois de
  // um swap e reconciliava contra a página errada (Codex, #118). Antes do SPA
  // o reload descartava o callback pendente; agora ele sobrevive à troca.
  let livePage = page;

  // pb-root-* no <html> aqui no <head> (síncrono) = paleta por página já no 1º
  // paint. Só no buildTabbar (DOMContentLoaded) fazia o Ajustes piscar de cor.
  if (page) root.classList.add("pb-root-" + page);

  // Viewport de app: safe areas + zoom travado (pinch/auto-zoom estica o
  // layout e "come" texto nas bordas; app nativo não tem zoom de UI)
  function fixViewport() {
    const m = document.querySelector('meta[name="viewport"]');
    if (m) m.content = "width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover";
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

  // 4º lugar da tab bar: "Notícias" (/changelog) é recurso Pro. Se o plano
  // libera, ela assume esse lugar (no lugar de "O que pedir") e some do menu
  // lateral. Free mantém "O que pedir". A decisão fica em cache (localStorage)
  // pra montar instantâneo; /auth/me só reconcilia depois.
  const NEWS_TAB = { href: "/changelog", label: "Notícias", icon:
    '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M3 6a1 1 0 0 1 1-1h13a1 1 0 0 1 1 1v12a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M18 9h2a1 1 0 0 1 1 1v8a2 2 0 0 1-2 2"/><path d="M7 9h7M7 12h7M7 15h4"/></svg>' };
  function newsAllowed() {
    try { return localStorage.getItem("pbNewsTab") === "1"; } catch (_) { return false; }
  }
  function fourthTab() { return newsAllowed() ? NEWS_TAB : TABS[2]; } // TABS[2] = "O que pedir"

  // Pergunta o plano e reconcilia o 4º tab + o item do menu lateral. Como o
  // swap não muda posição nem quantidade de abas, atualiza o tab NO LUGAR
  // (sem remexer no dock). Pro: "Notícias" no rodapé, fora do menu.
  function syncNewsTab(bar) {
    fetch("/auth/me", { credentials: "same-origin" })
      .then(r => (r.ok ? r.json() : null))
      .then(me => {
        if (!me) return;
        const isPro = ((me.plan || "free") + "").trim().toLowerCase() === "pro";
        let prev = false;
        try { prev = localStorage.getItem("pbNewsTab") === "1"; } catch (_) {}
        try { localStorage.setItem("pbNewsTab", isPro ? "1" : "0"); } catch (_) {}
        const sidenavNews = document.querySelector('.sidenav-item[href="/changelog"]');
        if (sidenavNews) sidenavNews.style.display = isPro ? "none" : "";
        if (isPro === prev) return; // cache já estava certo
        const t = fourthTab();
        const a = bar.querySelector('.pb-tab[href="/comandos-app"], .pb-tab[href="/changelog"]');
        if (!a) return;
        a.setAttribute("href", t.href);
        const ico = a.querySelector(".pb-tab-ico"); if (ico) ico.innerHTML = t.icon;
        const lbl = a.querySelector("span:last-child"); if (lbl) lbl.textContent = t.label;
        const active = PAGES[t.href] === livePage; // viva, não a de boot (SPA)
        a.classList.toggle("active", active);
        if (active) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
      })
      .catch(() => {});
  }

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
    // no <html> também: o fundo do CANVAS (o que o elástico revela) vem do
    // html, e ele precisa acompanhar a paleta de cada tela
    root.classList.add("pb-root-" + page);

    const bar = document.createElement("nav");
    bar.className = "pb-tabbar";
    bar.setAttribute("aria-label", "Navegação principal");
    const tabHtml = t => {
      const active = PAGES[t.href] === page;
      return `<a class="pb-tab${active ? " active pb-live" : ""}" href="${t.href}"` +
        `${active ? ' aria-current="page"' : ""}>` +
        `<span class="pb-tab-ico">${t.icon}</span><span>${t.label}</span></a>`;
    };
    const fabHtml =
      '<a class="pb-tab pb-tab-fab" href="/app?lancar=1" aria-label="Lançar"><span>' +
      '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" ' +
      'stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></span></a>';
    // A chapa e a bolha são irmãs das abas: o path desenha a silhueta inteira
    // (cantos + soquete) e a bolha é um elemento próprio, arrastável.
    bar.innerHTML =
      '<div class="pb-dock-glow" aria-hidden="true"></div>' +
      '<svg class="pb-dock-skin" aria-hidden="true" focusable="false">' +
      '<defs>' +
      '<linearGradient id="pbDockPlate" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#23212A"/><stop offset="1" stop-color="#131216"/>' +
      "</linearGradient>" +
      '<linearGradient id="pbDockRim" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#fff" stop-opacity=".24"/>' +
      '<stop offset=".5" stop-color="#fff" stop-opacity=".07"/>' +
      '<stop offset="1" stop-color="#fff" stop-opacity=".03"/>' +
      "</linearGradient></defs>" +
      '<path class="pb-dock-fill" fill="url(#pbDockPlate)" stroke="url(#pbDockRim)" stroke-width="1"/>' +
      "</svg>" +
      tabHtml(TABS[0]) + tabHtml(TABS[1]) + fabHtml + tabHtml(fourthTab()) + tabHtml(TABS[3]) +
      '<span class="pb-dock-bead" aria-hidden="true"><span class="pb-dock-bead-ico"></span></span>';
    bar.querySelector(".pb-tab-fab").addEventListener("click", fabLaunch);
    document.body.appendChild(bar);
    initDock(bar);
    syncNewsTab(bar);
  }

  // ─── Dock "menisco" ──────────────────────────────────────────────────────
  // A aba ativa não ganha um indicador que desliza: a chapa DERRETE em volta
  // da bolha. O soquete é paramétrico — cada ombro é um círculo tangente à
  // borda de cima E à bolha, então |C_ombro − C_bolha| = s + rb e a
  // meia-largura do soquete cai da conta (reachOf). Tangência exata nos dois
  // lados = solda lisa, sem quina onde a borda vira socket.
  // r/rb/s vêm do CSS (measure() relê a cada layout) — ver --pb-dock-* lá.
  const DK = {
    h:    66,   // altura da chapa (bate com o CSS)
    r:    20,   // raio dos cantos
    rb:   22,   // raio da bolha
    by:    2,   // profundidade do centro da bolha (+ = abaixo da borda de cima)
    s:    22,   // raio base do ombro (o filete soldado)
    vmax:  2.4, // px/frame que satura a inclinação por velocidade
  };

  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);

  // meia-largura do soquete pra um ombro de raio s
  function reachOf(s, rb, by) {
    const a = (s + rb) * (s + rb) - (s - by) * (s - by);
    return a > 0 ? Math.sqrt(a) : 0;
  }
  // inverso de reachOf: maior ombro cujo soquete ainda cabe em L de chapa
  function shoulderFor(L, rb, by) {
    return (L * L - rb * rb + by * by) / (2 * (rb + by));
  }

  // Silhueta inteira: borda de cima (com o soquete em bx) + cantos + base.
  function socketPath(W, bx, by, sL, sR) {
    const h = DK.h, r = DK.r, rb = DK.rb;
    const dl = reachOf(sL, rb, by);
    const dr = reachOf(sR, rb, by);
    // tangência ombro↔bolha: no segmento que une os centros, a s do ombro
    const tan = (cx, s) => {
      const dx = bx - cx, dy = by - s;
      const d = Math.hypot(dx, dy) || 1;
      return [cx + (s * dx) / d, s + (s * dy) / d];
    };
    const a = tan(bx - dl, sL);
    const z = tan(bx + dr, sR);
    const n = v => v.toFixed(2);
    return (
      `M ${r} 0 L ${n(bx - dl)} 0` +
      ` A ${n(sL)} ${n(sL)} 0 0 1 ${n(a[0])} ${n(a[1])}` +   // ombro de trás
      ` A ${rb} ${rb} 0 0 0 ${n(z[0])} ${n(z[1])}` +          // a tigela
      ` A ${n(sR)} ${n(sR)} 0 0 1 ${n(bx + dr)} 0` +          // ombro da frente
      ` L ${W - r} 0 A ${r} ${r} 0 0 1 ${W} ${r}` +
      ` L ${W} ${h - r} A ${r} ${r} 0 0 1 ${W - r} ${h}` +
      ` L ${r} ${h} A ${r} ${r} 0 0 1 0 ${h - r}` +
      ` L 0 ${r} A ${r} ${r} 0 0 1 ${r} 0 Z`
    );
  }

  function initDock(bar) {
    const tabs = Array.prototype.slice.call(
      bar.querySelectorAll(".pb-tab:not(.pb-tab-fab)"));
    const path = bar.querySelector(".pb-dock-fill");
    const skin = bar.querySelector(".pb-dock-skin");
    const glow = bar.querySelector(".pb-dock-glow");
    const bead = bar.querySelector(".pb-dock-bead");
    const beadIco = bar.querySelector(".pb-dock-bead-ico");
    if (!tabs.length || !path || !bead) return;

    const smooth = !(window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);

    // let, não const: o onNavigate do pb-nav reaponta a "aba da página atual"
    // a cada swap client-side. Com const a atribuição lançava TypeError
    // (engolido), home ficava na aba de boot e voltar pra ela não navegava.
    let home = Math.max(0, tabs.findIndex(a => a.classList.contains("active")));
    let W = 0, stops = [], x = 0, target = 0, vel = 0, prev = 0;
    let live = home, raf = 0, running = false, drag = null;

    function measure() {
      const cs = getComputedStyle(bar);
      const px = (name, fallback) => {
        const v = parseFloat(cs.getPropertyValue(name));
        return isFinite(v) && v > 0 ? v : fallback;
      };
      DK.r  = px("--pb-dock-r", 20);
      DK.rb = px("--pb-dock-rb", 22);
      DK.s  = px("--pb-dock-s", 22);

      const bb = bar.getBoundingClientRect();
      W = bb.width;
      stops = tabs.map(a => {
        const r = a.getBoundingClientRect();
        return r.left - bb.left + r.width / 2;
      });
      skin.setAttribute("viewBox", `-4 -4 ${W + 8} ${DK.h + 8}`);
      target = stops[live];
      if (!drag && !running) { x = target; prev = x; }
      draw();
    }

    function draw() {
      if (!W) return;
      const q = clamp(vel / DK.vmax, -1, 1);
      const mag = Math.abs(q);
      const bx = clamp(x, DK.r + 1, W - DK.r - 1);

      // Chapa disponível de cada lado até o canto arredondado. Se nem a
      // tigela nua couber, a bolha sobe um tico (o soquete estreita junto) —
      // é o que impede a solda de invadir o canto nas abas das pontas.
      const gapL = Math.max(1, bx - DK.r);
      const gapR = Math.max(1, W - DK.r - bx);
      const gap = Math.min(gapL, gapR);
      let by = DK.by;
      if (gap < DK.rb) by = Math.min(by, -Math.sqrt(DK.rb * DK.rb - gap * gap));

      // Velocidade inclina a superfície: o ombro de trás se estica, o da
      // frente aperta (q > 0 = indo pra direita).
      const sL = clamp(DK.s * (1 + 0.06 * mag + 0.40 * q), 0,
        Math.max(0, shoulderFor(gapL, DK.rb, by)));
      const sR = clamp(DK.s * (1 + 0.06 * mag - 0.40 * q), 0,
        Math.max(0, shoulderFor(gapR, DK.rb, by)));

      path.setAttribute("d", socketPath(W, bx, by, sL, sR));
      bead.style.transform = `translate3d(${bx.toFixed(2)}px, ${by.toFixed(2)}px, 0)`;
      if (glow) glow.style.transform = `translate3d(${bx.toFixed(2)}px, 0, 0)`;
      // Quem está debaixo da bolha esconde o próprio ícone
      for (let i = 0; i < tabs.length; i++) {
        tabs[i].classList.toggle("pb-under", Math.abs(stops[i] - bx) < DK.rb + 4);
      }
    }

    function tick() {
      if (drag) {
        vel = x - prev;
        prev = x;
      } else if (smooth) {
        vel = (vel + (target - x) * 0.20) * 0.76;
        x += vel;
        prev = x;
        if (Math.abs(target - x) < 0.2 && Math.abs(vel) < 0.2) {
          x = target; vel = 0; running = false;
        }
      } else {
        x = target; vel = 0; prev = x; running = false;
      }
      draw();
      raf = running || drag ? requestAnimationFrame(tick) : 0;
    }
    function start() {
      running = true;
      if (!raf) raf = requestAnimationFrame(tick);
    }

    function setLive(i) {
      if (i === live) return;
      live = i;
      tabs.forEach((a, k) => a.classList.toggle("pb-live", k === i));
      beadIco.innerHTML = TABS[i].icon;      // reinicia o pop do ícone
    }
    const nearest = px => {
      let best = 0;
      for (let i = 1; i < stops.length; i++) {
        if (Math.abs(stops[i] - px) < Math.abs(stops[best] - px)) best = i;
      }
      return best;
    };
    function goTo(i, navigate) {
      setLive(i);
      target = stops[i];
      start();
      if (navigate && i !== home) {
        const href = tabs[i].getAttribute("href");
        // pb-nav (se ativo e a rota é convertida) troca a tela sem recarregar;
        // o onNavigate lá embaixo sincroniza o dock quando o mount terminar.
        // Senão, navegação de documento igual sempre foi.
        const nav = () => { if (!(window.PBNav && PBNav.go(href))) location.href = href; };
        if (smooth) setTimeout(nav, 190);
        else nav();
      }
    }

    beadIco.innerHTML = TABS[home].icon;

    tabs.forEach((a, i) => {
      a.addEventListener("click", ev => {
        // clique com modificador (abrir em outra aba) segue o link normal
        if (ev.button > 0 || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
        ev.preventDefault();          // a chapa derrete antes de navegar
        goTo(i, true);
      });
    });

    // Arrasto da bolha: preview ao vivo (a aba acende ao passar) e, ao soltar,
    // encaixa na mais próxima projetando um tico da velocidade.
    bead.addEventListener("pointerdown", ev => {
      if (ev.button > 0) return;
      const bb = bar.getBoundingClientRect();
      drag = { id: ev.pointerId, left: bb.left, off: ev.clientX - bb.left - x };
      try { bead.setPointerCapture(ev.pointerId); } catch (_) {}
      bar.classList.add("pb-dock-dragging");
      start();
      ev.preventDefault();
    });
    bead.addEventListener("pointermove", ev => {
      if (!drag || ev.pointerId !== drag.id) return;
      x = clamp(ev.clientX - drag.left - drag.off, stops[0], stops[stops.length - 1]);
      setLive(nearest(x));
      ev.preventDefault();
    });
    const finish = (ev, canceled) => {
      if (!drag || (ev && ev.pointerId !== drag.id)) return;
      drag = null;
      bar.classList.remove("pb-dock-dragging");
      // pointercancel = o sistema tomou o gesto no meio (giro de tela, gesto
      // do iOS). Não é escolha do usuário: a bolha volta pra aba da página
      // atual em vez de navegar pra onde ela tinha parado.
      if (canceled) { goTo(home, false); return; }
      goTo(nearest(clamp(x + vel * 5, stops[0], stops[stops.length - 1])), true);
    };
    bead.addEventListener("pointerup", ev => finish(ev, false));
    bead.addEventListener("pointercancel", ev => finish(ev, true));

    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", () => setTimeout(measure, 120));
    // Fontes/ícones chegando depois mudam a largura das abas → remede
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(measure).catch(() => {});

    // Navegação client-side (pb-nav.js): o motor troca a tela sem recarregar,
    // então o dock é o MESMO objeto vivo — aqui ele se sincroniza ao fim de
    // cada mount (tap ou botão voltar): bolha, aba ativa e "home" (a aba da
    // página atual, referência do pointercancel e do clique-na-mesma-aba).
    if (window.PBNav && PBNav.enabled) {
      PBNav.onNavigate = key => {
        livePage = key;   // callbacks assíncronos (syncNewsTab) leem a página viva
        // Glifos de texto (☰, ▾) da página recém-montada: o hardenGlyphs do
        // boot não alcança DOM que chegou por swap — re-roda (é idempotente).
        hardenGlyphs();
        const i = tabs.findIndex(a => PAGES[a.getAttribute("href")] === key);
        if (i < 0) return;
        home = i;
        setLive(i);
        target = stops[i];
        start();
        tabs.forEach((a, k) => {
          a.classList.toggle("active", k === i);
          if (k === i) a.setAttribute("aria-current", "page");
          else a.removeAttribute("aria-current");
        });
      };
    }
  }

  // Glifos de texto (☰, 🐷) viram SVG/imagem — WebView pode não ter as fontes
  function hardenGlyphs() {
    const burger =
      '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h16"/></svg>';
    document.querySelectorAll(".sidenav-toggle").forEach(el => {
      el.innerHTML = burger;
    });
    // Botão da conta: ícone de pessoa. No dashboard o slot é .hamburger-icon
    // (era um segundo hambúrguer, confundia); na Início é .user-caret, um "▾"
    // que sozinho no círculo não diz que ali mora a conta.
    document.querySelectorAll(".hamburger-icon, .user-caret").forEach(el => {
      el.innerHTML =
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
        '<circle cx="12" cy="8" r="3.6"/><path d="M5 20a7 7 0 0 1 14 0"/></svg>';
    });
    // FAB do chat: usa a mesma imagem do site (sticker "hello", já no HTML). O
    // override antigo trocava pela mascote de corpo inteiro (mascot.webp), que
    // ficava pequena e torta dentro do círculo — removido pra unificar site/app.
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
  // A query fica declarada AQUI, no único lugar que a consome: ela não
  // participa do gate do modo app (isso é só UA/standalone) e, solta no topo do
  // arquivo, parecia resíduo do preview ?pbapp=1 removido — candidata a virar
  // ReferenceError na próxima limpeza.
  function maybeOpenLaunch() {
    const qs = new URLSearchParams(location.search);
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
      '<img src="/brand/icon.png?v=2" alt="" />';
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

  // Login Google no app: o botão dispara o fluxo nativo (ASWebAuthenticationSession
  // com Face ID/autofill) em vez de navegar o WebView pro Google. O nativo faz
  // o OAuth e devolve, carregando /d/{code} aqui pra logar. A ponte é checada
  // NO CLIQUE (não no load): no cold launch o nativo pode registrar o handler
  // depois do DOMContentLoaded — checar cedo perdia o fluxo com Face ID.
  // Fallback: sem ponte (build antigo), o link segue o fluxo web normal.
  function wireGoogleLogin() {
    document.querySelectorAll('a[href="/auth/google/start"], a[href^="/auth/google/start"]').forEach(a => {
      a.addEventListener("click", ev => {
        const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pbAuth;
        if (!bridge) return;
        ev.preventDefault();
        bridge.postMessage("google");
      });
    });
  }

  // Push notification (só app iOS nativo + usuário logado). Pede pro nativo
  // registrar no APNs; o device token volta em window.PBPush.onToken, que faz
  // o POST autenticado (reusa os cookies de sessão do WebView). PWA e navegador
  // NÃO têm a ponte pbPush → no-op (push em PWA é fase futura, canal diferente).
  function pbCookie(name) {
    const m = document.cookie.split("; ").find(r => r.startsWith(name + "="));
    return m ? decodeURIComponent(m.split("=").slice(1).join("=")) : "";
  }
  function wirePush() {
    if (!page) return;        // só em página logada (home/app/settings/comandos)
    // A ponte nativa pbPush pode nascer DEPOIS do DOMContentLoaded (no cold
    // launch o AppDelegate registra o handler quando o WebView existe). Por
    // isso tentamos por alguns segundos em vez de checar uma vez só — igual o
    // wireGoogleLogin, que checa a ponte no clique e não no load.
    let tries = 0;
    (function attempt() {
      const bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.pbPush;
      if (!bridge) {
        if (++tries < 20) setTimeout(attempt, 500);  // ~10s
        return;                 // PWA/navegador: a ponte nunca aparece → no-op
      }
      window.PBPush = window.PBPush || {};
      window.PBPush.onToken = function (token, environment) {
        if (!token) return;
        const csrf = pbCookie("csrf_token");
        fetch("/api/push/register", {
          method: "POST",
          credentials: "include",
          headers: Object.assign(
            { "Content-Type": "application/json" },
            csrf ? { "X-CSRF-Token": csrf } : {}
          ),
          body: JSON.stringify({ token: token, platform: "ios", environment: environment || "production" }),
        }).catch(() => {});
      };
      bridge.postMessage("register");
    })();
  }

  // ─── Puxar pra atualizar ─────────────────────────────────────────────────
  // Medido no aparelho (iPhone 17 Pro / iOS 26): o WebView NÃO reporta scrollY
  // negativo durante o elástico do topo — ele trava em 0. Então não dá pra ler
  // o overscroll nativo. O que dá é CANCELAR o elástico (o touchmove no topo é
  // cancelável) e desenhar o gesto por conta própria.
  //
  // O conteúdo não se move junto: um transform em html/body viraria bloco de
  // contenção e arrastaria tudo que é position:fixed — a tab bar, o FAB do
  // chat, o toast. Só o indicador desce, por cima da página.
  //
  // O que "atualizar" significa é da página, não daqui: quem sabe se refazer
  // expõe window.PBRefresh (devolvendo promise). Sem isso cai no reload — que
  // nas telas paradas (Ajustes, O que pedir) é barato e não perde estado.
  const PTR = {
    threshold: 62,   // puxão que arma o refresh
    hold:      66,   // onde o indicador para enquanto atualiza
    rubber:   280,   // resistência: maior = elástico mais duro
    max:      150,   // teto do puxão
    floor:    500,   // tempo mínimo girando (sumir na hora parece que falhou)
    watchdog: 12000, // PBRefresh pendurado não deixa o indicador girando pra sempre
  };

  function initPullToRefresh() {
    if (!page) return;                        // só nas telas logadas do app
    if (!("ontouchstart" in window)) return;  // desktop (PWA/devtools) não tem gesto

    const el = document.createElement("div");
    el.className = "pb-ptr";
    el.setAttribute("aria-hidden", "true");
    const r = 17, circ = 2 * Math.PI * r;
    el.innerHTML =
      '<span class="pb-ptr-disc"><img src="/brand/mascot.webp" alt="" />' +
      '<svg viewBox="0 0 40 40" aria-hidden="true">' +
      `<circle class="pb-ptr-track" cx="20" cy="20" r="${r}"/>` +
      `<circle class="pb-ptr-arc" cx="20" cy="20" r="${r}"/></svg></span>`;
    document.body.appendChild(el);
    const arc = el.querySelector(".pb-ptr-arc");
    arc.style.strokeDasharray = circ.toFixed(1);

    const smooth = !(window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches);

    let pull = 0, startY = 0, startX = 0, tracking = false, busy = false, raf = 0;
    let dragged = false;    // este gesto puxou de verdade (tap nunca arma)
    let inFlight = null;    // PBRefresh pendente (o watchdog libera o ciclo, não o pedido)

    // Nas telas do app o CONTEÚDO desce junto com o puxão — estilo nativo do
    // iOS — e o indicador aparece no vão que abre sob o status bar. O transform
    // mora no `.page`, o único wrapper de conteúdo; dock, FAB, toasts e overlays
    // são irmãos do `.page` e ficam ancorados, então mover o `.page` não arrasta
    // o chrome (e não abre faixa vazia embaixo).
    // Em Ajustes o `#bankpick-overlay` (position:fixed) é filho direto do
    // <body>, IRMÃO do `.page` (não descendente — confirmado no DOM). Então o
    // transform do puxão não vira o containing block dele nem o desloca. Se um
    // dia mover pra DENTRO do `.page`, o transform passaria a arrastá-lo — aí
    // volta pra fora, ou bloqueia abrir enquanto o transform estiver ativo.
    const moveContent = (page === "home" || page === "app" ||
                         page === "settings" || page === "changelog");
    const contentEl = moveContent ? document.querySelector(".page") : null;

    // Fetches pendentes DA PÁGINA (não do puxão): o fallback de reload não
    // pode atropelar um save em voo — o PATCH das preferências de notificação
    // dos Ajustes, por exemplo, seria abortado e a escolha recém-aceita pela
    // UI se perderia. Contador genérico em vez de contrato novo por página.
    // Mesmo padrão do auth-refresh.js, que também envelopa o window.fetch (lá
    // pra renovar 401, aqui só pra contar). Este wrap fica por FORA do dele:
    // o retry interno do 401 usa o fetch nativo direto, então conta como um
    // pedido só — o contador segue ≥1 até a sequência inteira assentar.
    let pageFetches = 0;
    if (window.fetch) {
      const nativeFetch = window.fetch;
      window.fetch = function () {
        pageFetches++;
        const dec = () => { pageFetches--; };
        let r;
        try {
          r = nativeFetch.apply(this, arguments);
        } catch (err) {
          // throw síncrono (argumento inválido) não pode vazar o contador —
          // preso em >0, o reload ficaria recusado pra sempre na página.
          dec();
          throw err;
        }
        r.then(dec, dec);
        return r;
      };
    }

    const damp = o => PTR.rubber * (1 - 1 / (o / PTR.rubber + 1));
    // Inverso do damp: dado um `pull`, o deslocamento de dedo que o produz.
    // Usado pra rebasear um puxão que começa em cima da molinha de recolher —
    // o gesto novo continua do offset residual em vez de saltar (o damp sempre
    // recebe pull < rubber, então o denominador nunca zera).
    const dampInv = p => PTR.rubber * p / Math.max(1, PTR.rubber - p);
    let startPullOffset = 0;   // dy virtual do residual retido no touchstart
    const atTop = () =>
      (window.scrollY || (document.scrollingElement || {}).scrollTop || 0) <= 0;

    function draw() {
      const p = Math.min(1, pull / PTR.threshold);
      if (contentEl) {
        // Conteúdo desce por inteiro (a molinha do rAF é quem anima).
        contentEl.style.transform =
          pull > 0 ? "translate3d(0," + pull.toFixed(1) + "px,0)" : "";
        // Indicador fica no vão que abriu: sobe atrás do status bar quando o
        // puxão é curto (translate negativo + opacity 0) e desce pra ~meio do
        // vão conforme cresce. Metade da velocidade do conteúdo = "revelado"
        // por trás dele, não colado ao topo do conteúdo.
        el.style.transform =
          "translate3d(0," + ((pull - 40) * 0.5).toFixed(1) + "px,0) scale(" +
          (0.5 + 0.5 * p).toFixed(3) + ")";
      } else {
        el.style.transform =
          "translate3d(0," + pull.toFixed(1) + "px,0) scale(" + (0.5 + 0.5 * p).toFixed(3) + ")";
      }
      el.style.opacity = Math.min(1, p * 1.6).toFixed(2);
      if (!busy) arc.style.strokeDashoffset = (circ * (1 - p)).toFixed(1);
    }

    // will-change só enquanto o puxão está vivo: manter uma camada de
    // composição permanente no `.page` (subárvore inteira) é caro. Liga ao
    // começar o gesto/refresh, desliga quando o conteúdo assenta em 0. O `.page`
    // não tem transition no CSS, então a molinha do rAF anima sozinha.
    function setDragging(on) {
      if (!contentEl) return;
      contentEl.style.willChange = on ? "transform" : "";
    }

    function spring(goal) {
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      // Assentou no zero: solta a camada de composição do `.page`. Só quando
      // chega em 0 — no HOLD (refresh em curso) o conteúdo continua segurado
      // embaixo.
      const done = () => { if (goal === 0) setDragging(false); };
      if (!smooth) { pull = goal; draw(); done(); return; }
      (function step() {
        pull += (goal - pull) * 0.22;
        if (Math.abs(pull - goal) < 0.5) { pull = goal; raf = 0; draw(); done(); return; }
        draw();
        raf = requestAnimationFrame(step);
      })();
    }

    function finish(ok) {
      if (!busy) return;
      el.classList.remove("pb-ptr-busy");
      arc.style.strokeDasharray = circ.toFixed(1);   // volta a ser anel inteiro
      if (ok) { busy = false; spring(0); return; }
      // Falhou: avisa em âmbar em vez de recolher como se tivesse dado certo.
      // A página mantém o dado antigo na tela (é o certo — melhor dado velho
      // que tela destruída), então sem este aviso o usuário juraria que
      // atualizou.
      //
      // busy fica TRUE até o âmbar recolher: o pull ainda está em HOLD (66px,
      // acima do limiar), e liberar o toque aqui deixava um TAP parado no topo
      // commitar outro refresh — e o timeout deste âmbar recolhia o indicador
      // do refresh novo no meio. Âmbar é estado terminal do ciclo, não idle.
      el.classList.add("pb-ptr-fail");
      arc.style.strokeDashoffset = "0";
      setTimeout(() => {
        el.classList.remove("pb-ptr-fail");
        busy = false;
        spring(0);
      }, 900);
    }

    function run() {
      busy = true;
      el.classList.add("pb-ptr-busy");
      // O dasharray é inline (o puxão desenha o anel fechando com ele), e
      // inline vence classe CSS — então o arco curto do "girando" tem que ser
      // setado aqui. Sem isto o anel fica CHEIO girando, que é visualmente
      // idêntico a um anel parado: parecia travado durante o refresh.
      arc.style.strokeDasharray = "26 80";
      spring(PTR.hold);

      if (typeof window.PBRefresh !== "function") {
        // Tela que não sabe se refazer: recarrega — MENOS com digitação
        // pendente. Nos Ajustes os editores (nome/e-mail/telefone, senhas de
        // exportação/exclusão) vivem no fluxo normal da página; um reload
        // apagaria o que o usuário escreveu. "Pendente" = campo visível cujo
        // value difere do defaultValue: os editores são preenchidos via JS
        // (defaultValue fica vazio) e ficam display:none até abrir, então a
        // regra dispara exatamente com um editor aberto ou senha digitada.
        // pageFetches: um save da página em voo (PATCH) seria abortado pelo
        // reload — recusa e deixa ele terminar. (Só vale pro caminho de reload:
        // no caminho custom, gatear o refresh no contador global trava o gesto
        // se um fetch pendura além do watchdog — a serialização é por-view.)
        // Recusa com âmbar — o mesmo sinal de "não deu" da falha de rede.
        const dirty = Array.prototype.some.call(
          document.querySelectorAll("input, textarea"),
          f => f.offsetParent !== null && f.value !== f.defaultValue
        );
        if (dirty || pageFetches > 0) { finish(false); return; }
        setTimeout(() => location.reload(), 220);
        return;
      }
      const t0 = Date.now();
      let settled = false;
      const done = ok => {
        if (settled) return;
        settled = true;
        // piso de 500ms: sumir instantâneo pareceria que nada aconteceu
        setTimeout(() => finish(ok), Math.max(0, PTR.floor - (Date.now() - t0)));
      };
      // A falha NÃO é engolida: vira aviso no indicador. E os PBRefresh são
      // SERIALIZADOS: o watchdog libera o ciclo visual, não o pedido — sem a
      // fila, um pedido estourado seguia vivo e podia terminar DEPOIS do
      // puxão seguinte, sobrescrevendo resposta mais nova no DOM e no cache.
      //
      // Dois relógios, um por fase: o de fila cobre a espera atrás de um
      // pedido pendurado; o de pedido só começa quando o PBRefresh deste
      // ciclo dispara de verdade. Um timer único a partir do puxão deixava a
      // espera na fila consumir a janela do pedido — âmbar antes de qualquer
      // rede. E ciclo que estourou na fila NÃO roda depois: sem isso o
      // "falhou" rodava silencioso quando o pedido velho enfim assentava.
      // Estouro de relógio também SOLTA a corrente (inFlight = null): sem
      // isso, um PBRefresh que nunca assenta envenenava a fila pra sempre —
      // todo puxão seguinte esperava atrás dele, estourava sem nunca rodar, e
      // o refresh morria até recarregar a página. Custo assumido ao soltar:
      // se o pedido pendurado assentar MUITO depois, a escrita dele pode
      // repintar dado mais velho uma vez (caso patológico; sem a fila isso
      // acontecia com QUALQUER pedido acima do watchdog, e blindar de vez
      // exigiria sinal de geração dentro de cada loader).
      let started = false;
      const prev = inFlight;
      const p = Promise.resolve(prev)
        .catch(() => {})
        .then(() => {
          if (settled) return;                            // estourou esperando: não vira fantasma
          started = true;
          setTimeout(() => {                              // relógio do pedido real
            done(false);
            if (inFlight === chain) inFlight = null;
          }, PTR.watchdog);
          return window.PBRefresh();
        });
      // relógio da fila: só conta enquanto o pedido deste ciclo não começou
      setTimeout(() => {
        if (started) return;
        done(false);
        if (inFlight === chain) inFlight = null;
      }, PTR.watchdog);
      const chain = p.catch(() => {});
      inFlight = chain;
      p.then(() => done(true), () => done(false));
    }

    // Duas coisas mandam no gesto delas, e não no puxão da página:
    //
    //  1. Estar dentro de um overlay FIXO. Nesta base todo diálogo é
    //     position:fixed (.mfa-overlay e .bankpick-overlay dos Ajustes, .overlay
    //     do dashboard), assim como a tab bar e o FAB — arrastar em qualquer um
    //     deles nunca deveria puxar a página atrás. Vale MESMO QUE o diálogo
    //     não transborde: um passo curto do MFA cabe na tela, e ali o puxão
    //     viraria reload (Ajustes não tem PBRefresh) por cima dos códigos de
    //     backup, que só aparecem uma vez. Conteúdo de página vive no fluxo
    //     normal e nunca cai aqui; header sticky é `sticky`, não `fixed`.
    //
    //  2. Ser uma área com rolagem própria dentro do fluxo (lista, tabela).
    //
    // Regra medida em vez de lista de classes: lista envelhece — foi ela que
    // deixou .mfa-modal e .bankpick-list de fora na primeira versão.
    function ownsGesture(node) {
      for (let el = node; el && el !== document.body; el = el.parentElement) {
        if (el.nodeType !== 1) continue;
        const st = getComputedStyle(el);
        if (st.position === "fixed") return true;
        // Popover: absoluto empilhado ACIMA do conteúdo (dropdown da conta,
        // z-index 40). Arrastar dentro dele é gesto do popover, mesmo quando
        // o conteúdo cabe sem rolar. Inventário (grep absolute+z-index nos
        // CSS das 4 páginas): só o .user-dropdown está nessa faixa hoje;
        // decoração absoluta é z=0 e pointer-events:none, nunca vira target.
        if (st.position === "absolute" && st.zIndex !== "auto" &&
            parseInt(st.zIndex, 10) >= 10) return true;
        if ((st.overflowY === "auto" || st.overflowY === "scroll" || st.overflowY === "overlay") &&
            el.scrollHeight > el.clientHeight + 1) return true;
      }
      return false;
    }

    // Segundo dedo no meio do puxão CANCELA (não pausa): deixar o estado
    // armado fazia o primeiro touchend commitar refresh dentro de um gesto
    // multi-touch — nas telas sem PBRefresh, um reload inteiro.
    function cancelPull() {
      tracking = false;
      dragged = false;
      if (pull > 0) spring(0);
    }

    addEventListener("touchstart", ev => {
      if (busy) return;
      if (ev.touches.length !== 1) { cancelPull(); return; }
      if (ownsGesture(ev.target)) return;
      // Arrasto que NASCE num campo de texto é gesto do campo (seleção,
      // cursor), nunca puxão — e um puxão dali poderia virar reload por cima
      // da digitação. Os campos vivem no fluxo normal; ownsGesture não os vê.
      if (ev.target.closest && ev.target.closest("input, textarea, select, [contenteditable]")) return;
      tracking = atTop();
      startPullOffset = 0;
      // Retry rápido: se a molinha do ciclo anterior ainda está recolhendo,
      // o rAF dela seguiria empurrando pull pra zero por baixo do touchmove
      // — o puxão novo desabava no meio. Gesto aceito mata a molinha.
      //
      // Mas matar a molinha deixa `pull` no valor residual, e o primeiro
      // touchmove o trocava por damp(dy)≈0 — com o `.page` inteiro descendo,
      // isso é um salto visível de dezenas de px pra cima antes de seguir o
      // dedo. Rebaseia: guarda o dy virtual do residual pra somar no touchmove,
      // então o pull começa no residual e cresce contínuo. O offset entra SÓ no
      // cálculo do pull — a classificação dx/dy usa o dy cru (senão o offset
      // inflaria o dy e um swipe horizontal viraria puxão).
      if (tracking && raf) {
        cancelAnimationFrame(raf); raf = 0;
        if (pull > 0) startPullOffset = dampInv(pull);
      }
      dragged = false;
      startY = ev.touches[0].clientY;
      startX = ev.touches[0].clientX;
    }, { passive: true });

    addEventListener("touchmove", ev => {
      if (busy) return;
      if (ev.touches.length !== 1) { cancelPull(); return; }
      if (!tracking) return;
      const dy = ev.touches[0].clientY - startY;
      const dx = ev.touches[0].clientX - startX;
      // Gesto horizontal (carrossel, tabela que rola de lado) não é puxão.
      // Só entrega o gesto de volta enquanto ESTE gesto ainda não puxou — no
      // meio dele um tremido lateral não pode cancelar tudo. O predicado é
      // !dragged, não pull===0: um retry rápido mata a molinha do ciclo
      // anterior deixando pull residual > 0, e com pull===0 esse resíduo
      // desligava a detecção — um arrasto horizontal virava puxão.
      if (!dragged && Math.abs(dx) > Math.abs(dy)) {
        tracking = false;
        if (pull > 0) spring(0);        // resíduo da molinha morta recolhe
        return;
      }
      if (dy <= 0) {                    // virou rolagem normal: devolve o gesto
        if (pull > 0) spring(0);        // recolhe na molinha, não em corte seco
        tracking = false;
        return;
      }
      // Sem spring aqui o pull ficava congelado no ar: release() com
      // tracking=false retorna cedo e nunca recolhe. Mesma família do bail
      // de dy<=0 logo acima.
      if (!atTop()) { tracking = false; if (pull > 0) spring(0); return; }
      if (ev.cancelable) ev.preventDefault();   // mata o elástico nativo
      if (!dragged) setDragging(true);          // primeira amostra: arma a camada de composição
      dragged = true;
      pull = Math.min(PTR.max, damp(dy + startPullOffset));
      draw();
    }, { passive: false });

    function release(canceled) {
      if (!tracking || busy) { tracking = false; return; }
      tracking = false;
      // dragged: só arma se ESTE gesto puxou. Sem isso, pull retido de um
      // ciclo anterior (ex.: o âmbar segurando em HOLD) deixava um tap commitar.
      if (!canceled && dragged && pull >= PTR.threshold) run(); else spring(0);
    }
    addEventListener("touchend", () => release(false), { passive: true });
    // touchcancel = o SISTEMA tomou o gesto no meio (giro de tela, troca de
    // app, gesto do iOS). Não é escolha do usuário, então recolhe sem
    // atualizar — senão um puxão interrompido viraria reload nas telas sem
    // PBRefresh. Mesma regra que a bolha do dock usa no pointercancel.
    addEventListener("touchcancel", () => release(true), { passive: true });

    // rAF congela com o app em segundo plano: ao voltar, termina a molinha.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !raf && !busy && pull !== 0) spring(0);
    });
  }

  function init() { fixViewport(); buildTabbar(); hardenGlyphs(); enhanceOverview(); enhanceSettings(); wireGoogleLogin(); wirePush(); maybeOpenLaunch(); initPullToRefresh(); }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
