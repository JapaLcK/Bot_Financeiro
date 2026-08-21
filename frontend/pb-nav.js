/**
 * pb-nav.js — navegação client-side entre as abas do modo app (Fase 1).
 *
 * O porquê: trocar de aba por location.href recarrega o documento, e o vão
 * entre descartar a página velha e pintar a nova aparece como piscada preta
 * no WKWebView — nenhum CSS alcança esse instante. Aqui a troca acontece
 * DENTRO de um documento só (fetch + swap em startViewTransition): sem
 * navegação de documento, o vão não existe.
 *
 * Ativação (POC): modo app (html.pb-app) + flag pbspa. Liga com ?pbspa=1
 * (persiste em localStorage, igual ao pbapp), desliga com ?pbspa=0.
 * Teste no iPhone: Safari → https://pigbankai.com/home?pbapp=1&pbspa=1
 *
 * Contrato por página (só as ROUTES abaixo):
 *   - scripts inline embrulhados em (PBPages.<key> ||= {inits:[]}).inits.push(fn)
 *   - último script da página (marcado data-pb-boot, que o motor NÃO executa):
 *     PBNav.boot("<key>") — no load MPA roda os inits; em mount SPA é o motor
 *     que os roda.
 *   - window.PBRefresh continua o contrato de refresh: o motor o salva/restaura
 *     por página e o chama em background ao voltar pra uma aba cacheada.
 *
 * Fallback sempre-navega: qualquer erro (fetch, timeout 5s, redirect de
 * auth/gate, rota não convertida) cai em location.href — o pior caso é o
 * comportamento de hoje, nunca tela travada.
 *
 * Limitações conhecidas da Fase 1 (documentadas no PR):
 *   - listeners globais de uma página desmontada seguem vivos (mexem em DOM
 *     desanexado: no-op visual); evicção com AbortSignal fica pra quando
 *     houver pressão de memória medida.
 *   - o puxar-pra-atualizar do app-mode captura o .page da página de boot;
 *     após um swap ele opera no modo simples (só indicador). Fase 2.
 */
(() => {
  "use strict";

  const qs = new URLSearchParams(location.search);
  if (qs.get("pbspa") === "0") { try { localStorage.removeItem("pbSpa"); } catch (_) {} }
  if (qs.get("pbspa") === "1") { try { localStorage.setItem("pbSpa", "1"); } catch (_) {} }
  let flag = null;
  try { flag = localStorage.getItem("pbSpa"); } catch (_) {}

  // pb-app já está no <html> (app-mode.js roda antes, síncrono no head)
  const enabled = document.documentElement.classList.contains("pb-app") &&
    flag === "1" &&
    typeof document.startViewTransition === "function" &&
    typeof DOMParser === "function";

  // Rotas convertidas — as chaves batem com o PAGES do app-mode.js
  const ROUTES = { "/home": "home", "/comandos-app": "comandos" };
  if (location.hostname === "localhost" || location.hostname === "127.0.0.1") {
    Object.assign(ROUTES, { "/home.html": "home", "/comandos-app.html": "comandos" });
  }

  window.PBPages = window.PBPages || {};

  const cache = {};        // key → {nodes, styles, title, scrollY, refresh, bodyClass}
  const booted = new Set(); // keys cujos scripts inline já executaram (nunca 2x: let/const)
  let currentKey = null;
  let seq = 0;             // geração: só a navegação mais recente monta

  // Base neutra quando a origem é opaca (preview/data:): só o pathname importa.
  const norm = href => {
    const base = location.origin && location.origin !== "null" ? location.origin : "http://pb.local";
    try { return new URL(href, base).pathname.replace(/\/+$/, "") || "/"; }
    catch (_) { return null; }
  };

  const hard = path => { location.href = path; };

  // Nós do shell (vivem no body mas pertencem ao app, não à página)
  const isShell = n => n.nodeType === 1 &&
    (n.classList.contains("pb-tabbar") || n.classList.contains("pb-ptr"));

  function runInits(key) {
    const p = window.PBPages[key];
    ((p && p.inits) || []).forEach(f => { try { f(); } catch (e) { console.error("[pb-nav] init " + key, e); } });
  }

  function setRootClass(key) {
    const root = document.documentElement;
    root.className = root.className.replace(/\bpb-root-\S+/g, "").replace(/\s+/g, " ").trim();
    root.classList.add("pb-root-" + key);
  }

  // Desmonta a página atual: nós do body (menos shell), estilos marcados,
  // título, rolagem e o PBRefresh dela. Só vai pro CACHE se a init assentou —
  // a prova é o window.PBRefresh presente (o contrato é exposto no FIM da
  // carga; ver home.html). Cachear página inacabada congelava o esqueleto pra
  // sempre: a init em voo morre ao consultar DOM desanexado e nunca é re-rodada
  // (apontamento do Codex no #118). Página sem o contrato (comandos) nunca
  // cacheia: remonta fresca — os listeners dela são todos de nó, re-init limpa.
  // DOM cacheado fica VIVO desanexado — estado (campo digitado) sobrevive.
  function unmountCurrent() {
    if (!currentKey) return;
    const e = {
      nodes: document.createDocumentFragment(),
      styles: [],
      title: document.title,
      scrollY: window.scrollY,
      refresh: window.PBRefresh,
      bodyClass: document.body.className,
    };
    document.querySelectorAll('head style[data-pb-page="' + currentKey + '"]')
      .forEach(s => { e.styles.push(s); s.remove(); });
    Array.prototype.slice.call(document.body.childNodes)
      .forEach(n => { if (!isShell(n)) e.nodes.appendChild(n); });
    if (e.refresh) cache[currentKey] = e;   // assentou: pode voltar do cache
    window.PBRefresh = undefined;
  }

  function mountPoint() { return document.querySelector(".pb-tabbar"); }

  const swap = mutate =>
    document.startViewTransition(mutate).finished.catch(() => {});

  // Roda DENTRO do callback do swap, atômico com a mutação do DOM: currentKey,
  // histórico e dock mudam juntos ou não mudam nada. Qualquer um deixado pra
  // depois do .finished abria janela de ~320ms em que um tap novo supersede e
  // o estado racha — a chave velha envenenava o cache (rodada 2 do Codex) e o
  // pushState nunca registrado deixava a navegação seguinte empurrar URL
  // duplicada, quebrando o Back (rodada 4).
  function commit(key, path, push) {
    currentKey = key;
    // try/catch: histórico lança em origem opaca (preview/embeds) — a troca
    // de tela nunca pode morrer por causa do pushState.
    if (push) { try { history.pushState({ pb: key }, "", path + location.hash); } catch (_) {} }
    // erro do hook NÃO derruba a navegação, mas também não é engolido mudo:
    // um catch vazio escondeu um TypeError que quebrava o dock (Codex, #118)
    if (window.PBNav.onNavigate) {
      try { window.PBNav.onNavigate(key); }
      catch (e) { console.error("[pb-nav] onNavigate", e); }
    }
  }

  async function mountCached(key, path, push, my) {
    await swap(() => {
      if (my !== seq) return;
      unmountCurrent();
      const e = cache[key];
      delete cache[key];             // os nós voltam pro DOM; a entrada morreu
      e.styles.forEach(s => document.head.appendChild(s));
      document.body.insertBefore(e.nodes, mountPoint());
      document.title = e.title;
      document.body.className = e.bodyClass;
      setRootClass(key);
      window.PBRefresh = e.refresh;
      window.scrollTo(0, e.scrollY);
      commit(key, path, push);
      // Dado fresco em background, reusando o contrato do puxar-pra-atualizar:
      // dado velho na tela agora, dado novo repintando quando chegar.
      if (window.PBRefresh) { try { window.PBRefresh(); } catch (_) {} }
    });
  }

  // Scripts externos da página nova que o documento ainda não tem (dedupe por
  // URL absoluta). Externos definem globals uma vez — nunca re-executam.
  function ensureExternalScripts(doc) {
    const have = new Set(Array.prototype.map.call(document.scripts, s => s.src).filter(Boolean));
    const need = Array.prototype.slice.call(doc.querySelectorAll("script[src]"))
      .map(s => new URL(s.getAttribute("src"), location.origin).href)
      .filter(u => !have.has(u));
    return Promise.all(need.map(u => new Promise((ok, bad) => {
      const s = document.createElement("script");
      s.src = u; s.onload = ok; s.onerror = () => bad(new Error("script " + u));
      document.head.appendChild(s);
    })));
  }

  async function mountNew(key, path, html, push, my) {
    const doc = new DOMParser().parseFromString(html, "text/html");

    await ensureExternalScripts(doc);
    if (my !== seq) return;

    // Executa os scripts inline UMA vez por página (data-pb-boot fica de fora:
    // boot é do load MPA; aqui quem roda os inits é o motor, dentro do swap).
    if (!booted.has(key)) {
      booted.add(key);
      doc.querySelectorAll("script:not([src]):not([data-pb-boot])").forEach(s => {
        const el = document.createElement("script");
        el.textContent = s.textContent;
        document.body.appendChild(el);
        el.remove();
      });
    }

    const newStyles = Array.prototype.map.call(
      doc.head.querySelectorAll("style"),
      s => {
        const c = document.createElement("style");
        c.textContent = s.textContent;
        c.setAttribute("data-pb-page", key);
        return c;
      });
    const bodyClass = doc.body.getAttribute("class") || "";
    const title = doc.title;

    await swap(() => {
      if (my !== seq) return;
      unmountCurrent();
      newStyles.forEach(s => document.head.appendChild(s));
      const frag = document.createDocumentFragment();
      Array.prototype.slice.call(doc.body.childNodes).forEach(n => frag.appendChild(document.importNode(n, true)));
      frag.querySelectorAll("script").forEach(s => s.remove());
      document.body.insertBefore(frag, mountPoint());
      document.title = title;
      document.body.className = (bodyClass ? bodyClass + " " : "") + "pb-page-" + key;
      setRootClass(key);
      window.scrollTo(0, 0);
      runInits(key);          // síncrono entra no snapshot; o resto é async normal
      commit(key, path, push);
    });
  }

  // Prefetch: o HTML das outras abas é aquecido em background logo após o
  // load e a cada troca. Sem isso, o fetch acontecia NO tap — a rede que a
  // recarga escondia atrás da piscada virava tela parada (medido no GATE 1:
  // "demora muito"). Com o texto já baixado, o mount é imediato. O HTML é só
  // o shell (esqueleto); dado vivo vem da init/PBRefresh — não fica velho.
  const warm = {};   // key → html (null = em voo)
  function warmUp() {
    Object.keys(ROUTES).forEach(path => {
      const key = ROUTES[path];
      if (key === currentKey || cache[key] || warm[key] !== undefined) return;
      warm[key] = null;
      fetch(path, { credentials: "same-origin", headers: { Accept: "text/html" } })
        .then(r => (r.ok && !r.redirected ? r.text() : Promise.reject()))
        .then(html => { warm[key] = html; })
        .catch(() => { delete warm[key]; });
    });
  }

  async function navigate(path, key, push) {
    const my = ++seq;                            // só a navegação mais recente monta
    const t0 = performance.now();
    const log = via => console.log("[pb-nav]", key, via,
      Math.round(performance.now() - t0) + "ms");
    // O try cobre TODOS os ramos (cache, warm e fetch): mountNew também rejeita
    // fora da rede do tap — ex.: script externo da página nova falhando ao
    // carregar (home puxa auth-refresh/modals quando o boot foi no comandos).
    // Sem isso o ramo warm deixava o usuário preso na tela velha, porque o
    // go() já tinha assumido a navegação (apontamento do Codex no #118).
    try {
      if (cache[key]) { await mountCached(key, path, push, my); log("cache"); return; }
      if (warm[key]) {                           // prefetch pronto: sem rede no tap
        const html = warm[key];
        delete warm[key];
        await mountNew(key, path, html, push, my);
        log("warm");
        setTimeout(warmUp, 400);
        return;
      }
      const r = await fetch(path, {
        credentials: "same-origin",
        headers: { Accept: "text/html" },
        signal: AbortSignal.timeout(5000),
      });
      if (my !== seq) return;                    // um tap mais novo venceu
      // Redirect = auth/gate (login, /precos): fluxo de verdade, navegação real
      if (r.redirected || !r.ok) { hard(path); return; }
      const html = await r.text();
      if (my !== seq) return;
      await mountNew(key, path, html, push, my);
      log("fetch");
      setTimeout(warmUp, 400);
    } catch (_) {
      if (my === seq) hard(path);                // qualquer falha: cai no MPA
    }
  }

  window.addEventListener("popstate", () => {
    if (!enabled || !currentKey) return;
    const path = norm(location.pathname);
    const key = ROUTES[path];
    if (!key) { location.reload(); return; }     // URL não convertida: MPA
    if (key === currentKey) return;
    navigate(path, key, false);
  });

  window.PBNav = {
    enabled,
    onNavigate: null,   // o dock do app-mode se pendura aqui pra sincronizar

    // true = o motor assume esta navegação; false = chame location.href
    go(href) {
      if (!enabled) return false;
      const path = norm(href);
      const key = path && ROUTES[path];
      if (!key) return false;
      if (key === currentKey) return true;       // já está nela
      navigate(path, key, true);
      return true;
    },

    // Chamado pelo load MPA da própria página (script data-pb-boot).
    boot(key) {
      if (enabled) {
        currentKey = key;
        booted.add(key);
        try { history.scrollRestoration = "manual"; } catch (_) {}
        try { history.replaceState({ pb: key }, "", location.pathname + location.search + location.hash); } catch (_) {}
        // marca os estilos de head desta página pra viajarem com ela no swap
        document.querySelectorAll("head style:not([data-pb-page])")
          .forEach(s => s.setAttribute("data-pb-page", key));
        // aquece as outras abas depois que a carga da página assentar
        setTimeout(warmUp, 1200);
      }
      runInits(key);
    },
  };
})();
