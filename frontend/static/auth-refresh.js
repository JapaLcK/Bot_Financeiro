/**
 * frontend/auth-refresh.js — Interceptor global de fetch que renova access
 * automaticamente em 401.
 *
 * Estratégia: monkey-patch window.fetch. Em request 401 que NÃO seja o próprio
 * /auth/refresh, dispara POST /auth/refresh, e re-tenta a request original.
 * Se refresh falhar, deixa o 401 passar pro caller decidir (geralmente redireciona
 * pra login). Refresh em paralelo é deduplicado.
 *
 * Servido como arquivo externo em /static/auth-refresh.js — incluído via <script>
 * no <head> das páginas autenticadas (dashboard, home, settings, onboarding).
 *
 * Também expõe `window.pbCsrfHeaders` — a implementação compartilhada do header
 * de CSRF, pra código novo não virar mais uma cópia (ver o bloco no fim).
 */
(() => {
  const _origFetch = window.fetch;
  let _refreshPromise = null;

  function _isOwnApi(url) {
    if (typeof url !== "string") url = (url && url.url) || "";
    if (!url) return false;
    if (url.startsWith("/")) return true;
    try {
      const u = new URL(url, window.location.origin);
      return u.host === window.location.host;
    } catch (_) { return false; }
  }

  function _isRefreshEndpoint(url) {
    if (typeof url !== "string") url = (url && url.url) || "";
    return url.includes("/auth/refresh");
  }

  function _getCsrfToken() {
    const m = document.cookie.split("; ").find(r => r.startsWith("csrf_token="));
    return m ? decodeURIComponent(m.split("=")[1]) : "";
  }

  async function _doRefresh() {
    if (_refreshPromise) return _refreshPromise;
    _refreshPromise = (async () => {
      try {
        const csrf = _getCsrfToken();
        const headers = { "Content-Type": "application/json" };
        if (csrf) headers["X-CSRF-Token"] = csrf;
        const r = await _origFetch("/auth/refresh", {
          method: "POST",
          credentials: "same-origin",
          headers,
        });
        // O refresh interno não passa pelo wrapper, então a limpeza é aplicada
        // aqui: um 401 nele é deslogue involuntário, e o backend limpa o cookie
        // de sessão nesse mesmo ramo (finance_bot_websocket_custom.py).
        await _comLimpeza("/auth/refresh", r);
        return r.ok;
      } catch (_) {
        return false;
      } finally {
        // Solta o lock na próxima volta do event loop
        setTimeout(() => { _refreshPromise = null; }, 0);
      }
    })();
    return _refreshPromise;
  }

  /** URL desta request, para as duas checagens abaixo. "" quando não dá. */
  function _caminho(input) {
    try {
      const u = typeof input === "string" ? input : (input && input.url) || "";
      return new URL(u, location.origin).pathname;
    } catch (_) { return ""; }
  }

  /**
   * As rotas que ENCERRAM A SESSÃO, e como se reconhece que encerraram.
   *
   * A lista não é "logout": é toda rota cujo backend chama
   * `_clear_session_cookies` (finance_bot_websocket_custom.py). São três, e o
   * critério de cada uma difere — por isso um mapa e não um array:
   *
   *   POST   /auth/logout    encerra quando dá certo
   *   DELETE /auth/account   idem — a exclusão agendada desloga na hora
   *   POST   /auth/refresh   encerra quando FALHA (401): refresh inválido é
   *                          deslogue involuntário, e o backend limpa o cookie
   *                          nesse mesmo ramo
   *
   * `tests/test_static_pages_routes.py` compara esta lista com as rotas que o
   * Python realmente tem, por `ast`. Duplicação inevitável — JS não importa
   * Python — então um teste prende as duas (§0.7). Uma quarta rota que limpe
   * cookie e não esteja aqui deixa o teste vermelho. Foi assim que a exclusão
   * de conta ficou de fora: consertei a instância e não a classe (Codex, #170).
   */
  const _SESSAO_ENCERRADA = {
    "/auth/logout":  function (resp) { return resp.ok; },
    "/auth/account": function (resp) { return resp.ok; },
    "/auth/refresh": function (resp) { return resp.status === 401; },
  };

  /**
   * O que SOBREVIVE ao fim de sessão. Tudo o mais é apagado.
   *
   * Lista do que PRESERVAR, não do que apagar, e a inversão é o ponto: uma
   * lista de "apagar isto" falha ABERTO — a próxima chave derivada de conta
   * nasce sobrevivendo ao logout, e ninguém percebe. Mesma lição da allowlist
   * do `service-worker.js`.
   *
   * O critério é observável: preferência DO APARELHO fica; qualquer coisa
   * derivada da CONTA sai. Apagar o tema no logout seria hostil, e manter o
   * "esconder saldo" é mais seguro que limpá-lo.
   *
   *   pigbank_theme          claro/escuro — preferência do aparelho
   *   pigbank_hide_balance   olho de esconder saldo — idem, e manter é o lado
   *                          seguro
   *   pbFabPos               posição da bolinha do chat
   *   pbDebug                flag de cronômetro do pb-nav
   *   pbSpa                  flag do motor SPA (sessionStorage, morre com a aba)
   *   finbot_logout_at       MECANISMO: outras abas escutam o storage event
   *                          desta chave para se deslogarem juntas. Apagá-la
   *                          quebraria o logout entre abas.
   */
  const _PRESERVA = new Set([
    "pigbank_theme", "pigbank_hide_balance", "pbFabPos", "pbDebug", "pbSpa",
    "finbot_logout_at",
  ]);

  function _apagaStorage(store) {
    try {
      Object.keys(store).forEach(function (k) {
        if (!_PRESERVA.has(k)) store.removeItem(k);
      });
    } catch (_) { /* storage bloqueado (Safari privado): nada a apagar */ }
  }

  /**
   * Fim de sessão → apaga o estado privado deste dispositivo.
   *
   * ENUMERADO, não remendado. Os quatro logouts limpavam subconjuntos
   * diferentes e nenhum limpava tudo:
   *
   *                     pigbank_menu_v1   pb_snap_*   pb_home_*
   *   dashboard.js           limpa          limpa       DEIXA
   *   home.html              DEIXA          limpa       limpa
   *   settings.html          DEIXA          DEIXA       DEIXA
   *   nav-auth.js            DEIXA          DEIXA       DEIXA
   *
   * E o que sobrava não é pouco: `pigbank_menu_v1` guarda e-mail, nome e plano;
   * `pb_home_<uid>` guarda snapshot, histórico, e-mail e nome. Sair pelo
   * Ajustes não limpava nada. As limpezas por página continuam onde estão —
   * são inofensivas e tirá-las seria outro PR.
   */
  function _limpaEstadoDoDispositivo() {
    _apagaStorage(window.localStorage);
    _apagaStorage(window.sessionStorage);
    // ORDEM: desregistra ANTES de apagar. Um worker ANTIGO ainda no controle
    // tem `cache.put` assíncrono no handler de fetch dele, e uma request em voo
    // noutra aba podia recriar o cache DEPOIS do delete (Codex, #170). O
    // unregister não mata o worker que já controla os clientes abertos — isso
    // só acontece quando eles descarregam, e o logout descarrega este —, mas
    // garante que nenhum load futuro pegue o worker velho.
    return _desregistraWorkers()
      .then(function () {
        if (!window.caches) return;
        return caches.keys().then(function (ks) {
          return Promise.all(ks.map(function (k) { return caches.delete(k); }));
        });
      })
      .catch(function () { /* sem SW ou sem CacheStorage: nada a limpar */ });
  }

  /**
   * Tira todo service worker do ar antes da limpeza.
   *
   * Custo aceito: o worker é re-registrado no próximo load de página
   * autenticada (`dashboard.js`, `home.html`), então a PWA fica sem offline
   * entre o logout e essa volta. Fim de sessão é exatamente quando esse custo
   * é barato — não há dado do usuário para servir offline.
   *
   * NÃO fecha a corrida sozinho, e isso está dito de propósito: o worker
   * desregistrado continua controlando os clientes já abertos até eles
   * descarregarem. O que fecha o caso residual é o `activate` do worker novo,
   * que apaga todo cache de nome diferente no próximo load do site.
   */
  function _desregistraWorkers() {
    try {
      var sw = navigator.serviceWorker;
      if (!sw || !sw.getRegistrations) return Promise.resolve();
      return sw.getRegistrations()
        .then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); })
        .catch(function () {});
    } catch (_) { /* sem service worker: nada a desregistrar */ }
    return Promise.resolve();
  }

  /**
   * Passa TODA resposta da nossa origem por aqui antes de devolvê-la.
   *
   * Este arquivo tem quatro `_origFetch`, e três produzem resposta nossa:
   *
   *   :n  refresh interno do `_doRefresh`   ← não passava (401 = deslogue
   *                                            involuntário e o backend limpa
   *                                            o cookie nesse ramo)
   *   :n  passagem cross-origin              não é rota nossa, fica de fora
   *   :n  request inicial                    já passava
   *   :n  retry depois do refresh OK         ← não passava (um
   *                                            `DELETE /auth/account` que leva
   *                                            401, renova e é refeito com
   *                                            sucesso encerra a sessão aqui)
   *
   * Os dois marcados eram buracos: a limpeza rodava num ponto só e as respostas
   * INTERNAS do interceptor saíam por baixo dela (Codex, #170). Enumerados em
   * vez de remendados um a um — é a terceira rodada deste PR na mesma classe,
   * e o CLAUDE.md §4 manda parar de remendar e enumerar quando isso acontece.
   *
   * Não limpa duas vezes no mesmo turno: cada ponto avalia o predicado da SUA
   * resposta, e os caminhos são exclusivos (ou o inicial encerrou, ou o refresh
   * falhou, ou o retry encerrou).
   */
  async function _comLimpeza(caminho, resp) {
    const fim = _SESSAO_ENCERRADA[caminho];
    if (fim && fim(resp)) await _limpaEstadoDoDispositivo();
    return resp;
  }

  window.fetch = async function(input, init) {
    // Requests pra fora da própria origem (Stripe, CDN, etc) seguem direto
    if (!_isOwnApi(input)) return _origFetch(input, init);

    const caminho = _caminho(input);

    // A limpeza é AGUARDADA antes de a resposta chegar a quem chamou. Quem
    // encerra a sessão navega assim que o fetch resolve
    // (`location.replace`/`reload`/`href`), e navegação descarta o documento:
    // uma limpeza disparada e esquecida não tem garantia de terminar, e o cache
    // privado sobrevive no aparelho compartilhado (Codex, #170).
    let resp = await _comLimpeza(caminho, await _origFetch(input, init));

    if (resp.status !== 401) return resp;

    // 401 no próprio refresh: não tenta de novo — caller redireciona pro login
    if (_isRefreshEndpoint(input)) return resp;

    // Tenta renovar e refazer a request original
    const ok = await _doRefresh();
    if (!ok) return resp;
    return _comLimpeza(caminho, await _origFetch(input, init));
  };

  // ── Modo app (iOS/Capacitor) ─────────────────────────────────────────────
  // O WebView do app anexa "PigBankApp" ao user agent. Dentro do app as
  // diretrizes da App Store proíbem link/CTA de compra externa, então os
  // anchors pra /precos somem via CSS e as páginas usam window.PB_IN_APP
  // pra trocar o redirect de paywall por uma tela neutra.
  // Exceção: elementos marcados com .pb-keep-in-app continuam visíveis — ex.:
  // o botão "Ver planos" do modal de paywall do dashboard, que precisa
  // redirecionar o usuário sem plano pra tela de planos.
  if (/PigBankApp/.test(navigator.userAgent)) {
    window.PB_IN_APP = true;
    document.documentElement.classList.add("pb-app");
    const st = document.createElement("style");
    st.textContent = 'html.pb-app a[href^="/precos"]:not(.pb-keep-in-app){display:none !important}';
    (document.head || document.documentElement).appendChild(st);
  }

  // ── CSRF ────────────────────────────────────────────────────────────────
  // Implementação única, exposta pras páginas que carregam este arquivo. O
  // helper está copiado em 8 lugares hoje (home.html, dashboard.js,
  // completar-cadastro.html, cadastro.html, login.html, reset-password.html,
  // nav-auth.js, admin-login.html); esta é a versão compartilhada pro código
  // novo não virar a nona cópia. Consolidar as 8 é PR de limpeza próprio.
  window.pbGetCookie = function (name) {
    const hit = document.cookie
      .split("; ")
      .find((row) => row.indexOf(name + "=") === 0);
    return hit ? hit.split("=")[1] : "";
  };

  window.pbCsrfHeaders = function (extra) {
    const headers = Object.assign({}, extra || {});
    const token = decodeURIComponent(window.pbGetCookie("csrf_token") || "");
    if (token) headers["X-CSRF-Token"] = token;
    return headers;
  };
})();
