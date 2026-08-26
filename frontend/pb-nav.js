/**
 * pb-nav.js — navegação client-side entre as abas do modo app (Fase 1).
 *
 * O porquê: trocar de aba por location.href recarrega o documento, e o vão
 * entre descartar a página velha e pintar a nova aparece como piscada preta
 * no WKWebView — nenhum CSS alcança esse instante. Aqui a troca acontece
 * DENTRO de um documento só (fetch + swap em startViewTransition): sem
 * navegação de documento, o vão não existe.
 *
 * Ativação (POC): modo app (html.pb-app) + flag pbspa, desligada por padrão.
 * DENTRO do app, ?pbspa=1 liga e ?pbspa=0 desliga; a flag vive em
 * sessionStorage — morre com a sessão da aba/app e não fica gravada no
 * aparelho. FORA do modo app a query não faz nada: nem liga, nem grava. Era
 * esse o buraco — um link ?pbspa=1 aberto no navegador armava a flag pra
 * sempre, sem UI de saída, e o motor acordava sozinho quando o app abrisse,
 * sem ninguém ter pedido. A chave pbSpa antiga do localStorage é apagada em
 * todo load (higiene) e ninguém mais a lê.
 * Teste no iPhone: no APP instalado, /home?pbspa=1 — não há como entrar em
 * modo app pelo Safari (o preview ?pbapp=1 foi removido do app-mode.js).
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

  // Modo app já decidido antes daqui. Atenção: html.pb-app tem DOIS setters,
  // e os dois rodam síncronos no <head>, sem defer:
  //   - auth-refresh.js:84-86 (só UA PigBankApp; também liga PB_IN_APP)
  //   - app-mode.js:46 (decisão em :42 — UA PigBankApp OU PWA standalone)
  // Nas duas únicas páginas que carregam este arquivo os dois já executaram:
  // home.html:455 (auth-refresh) e :458 (app-mode), antes do :459 daqui;
  // comandos-app.html:84 (app-mode, sem auth-refresh) antes do :85. Ou seja,
  // a classe pb-app já está no <html> quando esta linha executa — por isso o
  // gate abaixo pode usar a classe, e não repetir o sinal de ambiente
  // (UA/standalone) do app-mode. Se mexer em qualquer um dos dois setters,
  // lembre que este gate depende dos dois.
  const inApp = document.documentElement.classList.contains("pb-app");

  // Higiene da chave legada. A versão anterior gravava pbSpa no localStorage a
  // partir de ?pbspa=1, inclusive fora do modo app e sem UI de saída — quem
  // clicou num link de QA carrega a flag no aparelho até hoje. Este removeItem
  // limpa sozinho no primeiro load; o destravamento em si vem do gate, que
  // parou de LER o localStorage.
  // O try/catch é obrigatório: esta linha toca storage e, com storage
  // bloqueado (Safari privado, cookies off), o acesso ESTOURA. Sem o catch a
  // exceção mataria o resto do arquivo — inclusive o window.PBNav que o dock
  // do app-mode chama em toda troca de aba.
  try { localStorage.removeItem("pbSpa"); } catch (_) {}

  // O atalho de QA só existe DENTRO do modo app, e só pela sessão:
  // sessionStorage morre com a aba/app, então ?pbspa=1 serve pra testar sem
  // virar trava permanente, e ?pbspa=0 continua a saída explícita. Fora do
  // modo app nada é lido nem gravado. Mesmo try/catch, mesmo motivo (são
  // acessos a storage); se estourar, flag fica null e o motor não liga.
  let flag = null;
  try {
    if (inApp) {
      if (qs.get("pbspa") === "0") sessionStorage.removeItem("pbSpa");
      if (qs.get("pbspa") === "1") sessionStorage.setItem("pbSpa", "1");
      flag = sessionStorage.getItem("pbSpa");
    }
  } catch (_) {}

  // inApp entra aqui de novo de propósito, mesmo já sendo condição pra flag
  // existir: nenhum teste separa as duas barreiras (sem o guard de cima a flag
  // nem chega a ser lida fora do app), mas afrouxar UMA delas sozinha não pode
  // bastar pra religar o motor num navegador comum.
  const enabled = inApp &&
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

  // ── Cronômetro por fase ────────────────────────────────────────────────
  // "demora ~5s" não diz ONDE. Cada troca mede rede (ter o HTML), scripts
  // (externos da página nova), mutate (DOM + inits, síncrono) e vt (a
  // animação inteira até .finished). Com ?pbdebug=1 o resultado aparece na
  // PRÓPRIA tela — o aparelho é o único lugar onde isso se mede, e nem sempre
  // há Mac com o Web Inspector do lado.
  // PREGUIÇOSO de propósito. Como IIFE no escopo do módulo, isto lia o
  // localStorage em TODA página que carrega o pb-nav — inclusive fora do modo
  // app e com o motor desligado —, quebrando a garantia que o gate tem desde
  // que existe: ele não toca storage antes de decidir. O cronômetro só é usado
  // dentro de uma troca de página, ou seja, depois de o gate ter passado; ler
  // ali não custa nada e não vaza para quem nunca liga o SPA.
  let _debug = null;
  function debugOn() {
    if (_debug !== null) return _debug;
    if (qs.get("pbdebug") === "0") { try { localStorage.removeItem("pbDebug"); } catch (_) {} }
    if (qs.get("pbdebug") === "1") { try { localStorage.setItem("pbDebug", "1"); } catch (_) {} }
    try { _debug = localStorage.getItem("pbDebug") === "1"; } catch (_) { _debug = false; }
    return _debug;
  }

  function stopwatch(key) {
    const t0 = performance.now();
    let last = t0;
    const parts = [];
    return {
      mark(name) {
        const now = performance.now();
        parts.push(name + " " + Math.round(now - last));
        last = now;
      },
      done(via) {
        const total = Math.round(performance.now() - t0);
        const line = "[pb-nav] " + key + " " + via + " " + total + "ms (" + parts.join(", ") + ")";
        console.log(line);
        if (debugOn()) showDebug(line);
      },
    };
  }

  function showDebug(line) {
    let box = document.getElementById("pb-nav-debug");
    if (!box) {
      box = document.createElement("div");
      box.id = "pb-nav-debug";
      box.style.cssText = "position:fixed;left:8px;right:8px;top:env(safe-area-inset-top,8px);" +
        "z-index:99999;background:rgba(0,0,0,.86);color:#C6F11A;font:600 11px/1.45 ui-monospace,monospace;" +
        "padding:8px 10px;border-radius:10px;pointer-events:none;white-space:pre-wrap;";
      document.documentElement.appendChild(box);   // fora do body: sobrevive ao swap
    }
    box.textContent = line;
  }

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

  async function mountCached(key, path, push, my, sw) {
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
      sw.mark("mutate");
      // Dado fresco em background, reusando o contrato do puxar-pra-atualizar:
      // dado velho na tela agora, dado novo repintando quando chegar.
      if (window.PBRefresh) { try { window.PBRefresh(); } catch (_) {} }
    });
    sw.mark("vt");
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

  async function mountNew(key, path, html, push, my, sw) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    sw.mark("parse");

    await ensureExternalScripts(doc);
    sw.mark("scripts");
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
      sw.mark("mutate");
    });
    sw.mark("vt");
  }

  // Prefetch: o HTML das outras abas é aquecido em background logo após o
  // load e a cada troca. Sem isso, o fetch acontecia NO tap — a rede que a
  // recarga escondia atrás da piscada virava tela parada (medido no GATE 1:
  // "demora muito"). Com o texto já baixado, o mount é imediato. O HTML é só
  // o shell (esqueleto); dado vivo vem da init/PBRefresh — não fica velho.
  const warm = {};       // key → html pronto (null = ainda em voo)
  const inflight = {};   // key → promise do html em voo (o tap espera ESTA)
  function warmUp() {
    Object.keys(ROUTES).forEach(path => {
      const key = ROUTES[path];
      if (key === currentKey || cache[key] || warm[key] !== undefined) return;
      warm[key] = null;
      inflight[key] = fetch(path, { credentials: "same-origin", headers: { Accept: "text/html" } })
        .then(r => (r.ok && !r.redirected ? r.text() : Promise.reject()))
        .then(html => { warm[key] = html; delete inflight[key]; return html; })
        .catch(() => { delete warm[key]; delete inflight[key]; return null; });
    });
  }

  async function navigate(path, key, push) {
    const my = ++seq;                            // só a navegação mais recente monta
    const sw = stopwatch(key);
    // O try cobre TODOS os ramos (cache, warm e fetch): mountNew também rejeita
    // fora da rede do tap — ex.: script externo da página nova falhando ao
    // carregar (home puxa auth-refresh/modals quando o boot foi no comandos).
    // Sem isso o ramo warm deixava o usuário preso na tela velha, porque o
    // go() já tinha assumido a navegação (apontamento do Codex no #118).
    try {
      if (cache[key]) {
        await mountCached(key, path, push, my, sw);
        sw.done("cache");
        return;
      }
      // Prefetch: pronto OU em voo. Esperar o pedido que já está na rede é
      // sempre melhor que abrir um segundo — antes, `warm[key]` null (em voo)
      // era falsy e caía no fetch, duplicando a rede no tap.
      if (warm[key] !== undefined) {
        const html = await Promise.resolve(warm[key] || inflight[key]);
        sw.mark("net(warm)");
        delete warm[key];
        if (my !== seq) return;
        if (!html) { hard(path); return; }
        await mountNew(key, path, html, push, my, sw);
        sw.done("warm");
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
      sw.mark("net");
      if (my !== seq) return;
      await mountNew(key, path, html, push, my, sw);
      sw.done("fetch");
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
