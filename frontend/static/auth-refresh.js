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
    // Sem fast-path por "começa com /". Ele aceitava como nossa qualquer URL
    // com essa forma, e `//host/x` e `/\host/x` apontam para OUTRO host — a
    // segunda porque a WHATWG normaliza `\` como `/`. Bastava uma delas
    // terminando em `/auth/logout` para o pathname bater e a limpeza apagar
    // este aparelho. O parse abaixo cobre as duas formas, o caminho relativo
    // comum e a URL absoluta com uma regra só.
    //
    // ORIGEM, não host: host ignora o esquema, então numa página HTTPS o
    // `http://mesmo-host/auth/logout` passava por nosso. O navegador recusa
    // essa request por conteúdo misto, ela REJEITA, e a rejeição virou fim de
    // sessão — o aparelho seria apagado por um logout que nunca saiu (Codex).
    // "Mesma origem" é a definição certa aqui e é a que o cookie de sessão usa.
    try {
      return new URL(url, window.location.origin).origin === window.location.origin;
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
        // aqui. Só o 401 conta: o backend responde 400 quando o cookie de
        // refresh falta mas o access token ainda vale — sessão de pé, nada a
        // apagar (finance_bot_websocket_custom.py, #173).
        await _comLimpeza("/auth/refresh", r, "POST");
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
   *   POST   /auth/logout    encerra SEMPRE — 2xx, erro, ou resposta nenhuma
   *                          (`resp === null`, o fetch REJEITOU: offline, DNS,
   *                          captive portal). O critério não é o servidor, é o
   *                          chamador: os cinco donos de logout navegam para a
   *                          tela deslogada no `.finally`, sem olhar o status.
   *                          Se o estado ficasse, ele ficaria num aparelho que
   *                          mostra "saí" — resíduo privado no aparelho
   *                          compartilhado.
   *
   *                          Não é hipotético e não precisa de modo avião: o
   *                          cookie `csrf_token` dura 24h e só é reemitido em
   *                          método SEGURO quando falta, então uma aba aberta
   *                          mais de um dia manda o POST sem header e leva
   *                          403 do `csrf_middleware` — antes da rota. Prender
   *                          a limpeza ao `resp.ok` deixava esse caso e o 429
   *                          do `@limiter.limit("30/minute")` do lado errado.
   *
   *                          O MÉTODO entra na conta porque o predicado é
   *                          incondicional: um GET nesse pathname leva 405 e
   *                          não navega para lugar nenhum, então limpar ali
   *                          apagaria o aparelho de quem continua na página,
   *                          logado (Codex). Os outros dois não precisam da
   *                          checagem — o status já os prende, e um 405 não é
   *                          `ok` nem 401.
   *
   *                          O que isto NÃO faz: os cookies de sessão são
   *                          `httponly`, então JS nenhum os apaga, e num
   *                          logout que não chegou ao servidor a sessão
   *                          continua VIVA (`revoke_session` não rodou). O que
   *                          se ganha é não deixar e-mail, nome, plano e
   *                          snapshot na tela do próximo dono — não "deslogou".
   *   DELETE /auth/account   encerra quando dá certo, e SÓ com resposta. Numa
   *                          rejeição a exclusão não aconteceu, o chamador
   *                          (settings.html) mostra o toast e NÃO navega:
   *                          apagar o aparelho de quem continua logado seria
   *                          pior que o bug.
   *   POST   /auth/refresh   encerra quando dá 401, e SÓ 401 — rejeição é
   *                          rede fora, não fim de sessão: apagar aí destruía
   *                          o aparelho de quem só entrou no metrô. O status
   *                          desta rota responde "a sessão acabou?", não
   *                          classifica o erro: 400 é refresh ausente com
   *                          access ainda válido (sessão de pé, nada a
   *                          apagar), 401 é refresh invalidado/revogado ou
   *                          ausente sem access válido. Tratar todo 401 como
   *                          fim de sessão apagava o aparelho de quem só
   *                          errou a senha noutra rota (#173).
   *
   * Por que 401 aqui justifica apagar: o refresh token acabou, então não há
   * como renovar de novo — o que estiver no aparelho é lixo de uma sessão que
   * não volta. NÃO é "a sessão já acabou no servidor": o cookie de access pode
   * sobreviver até 15 min (e o backend nem o expira aqui — #175).
   *
   * `tests/test_static_pages_routes.py` compara esta lista com as rotas que o
   * Python realmente tem, por `ast`. Duplicação inevitável — JS não importa
   * Python — então um teste prende as duas (§0.7). Uma quarta rota que limpe
   * cookie e não esteja aqui deixa o teste vermelho. Foi assim que a exclusão
   * de conta ficou de fora: consertei a instância e não a classe (Codex, #170).
   */
  const _SESSAO_ENCERRADA = {
    "/auth/logout":  function (resp, metodo) { return metodo === "POST"; },
    "/auth/account": function (resp) { return !!resp && resp.ok; },
    "/auth/refresh": function (resp) { return !!resp && resp.status === 401; },
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
   *   finbot_reset_at        MECANISMO: carimbo do último "Recomeçar do zero";
   *                          home/dashboard descartam snapshot mais velho que
   *                          ele no restore. Apagá-lo no logout reabria o
   *                          flash na cadeia reset → logout → relogin na
   *                          mesma aba. Não é dado de conta: é um timestamp,
   *                          e a validação de userId do restore vem antes.
   */
  const _PRESERVA = new Set([
    "pigbank_theme", "pigbank_hide_balance", "pbFabPos", "pbDebug", "pbSpa",
    "finbot_logout_at", "finbot_reset_at",
  ]);

  /**
   * Recebe o NOME, não o objeto: `window.localStorage` é um getter que LANÇA
   * quando o site está com dados bloqueados (Chrome), e a avaliação do
   * argumento acontecia fora do `try`. O comentário prometia sobreviver a isso
   * e não sobrevivia — o erro subia por `_limpaEstadoDoDispositivo`, o Cache
   * Storage e o service worker ficavam intactos (o resíduo que esta limpeza
   * existe para remover) e o chamador recebia `SecurityError` no lugar do erro
   * de rede.
   */
  function _apagaStorage(nome) {
    try {
      const store = window[nome];
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
    _apagaStorage("localStorage");
    _apagaStorage("sessionStorage");
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
   * Cada ponto avalia o predicado da SUA resposta, e a limpeza roda uma vez:
   * ou o inicial encerrou, ou o refresh falhou, ou o retry encerrou. A cadeia
   * `logout → 401 → refresh OK → retry` limparia duas vezes agora que o logout
   * encerra em qualquer resposta, mas ela não existe — a rota não tem
   * dependência de auth, é no-op sem token e nunca responde 401
   * (`finance_bot_websocket_custom.py`). Se um dia responder, a limpeza é
   * idempotente e a segunda passada é desperdício, não dano.
   */
  /** O método desta request, como o navegador o veria. */
  function _metodo(input, init) {
    const m = (init && init.method)
      || (input && typeof input !== "string" && input.method)
      || "GET";
    return String(m).toUpperCase();
  }

  async function _comLimpeza(caminho, resp, metodo) {
    const fim = _SESSAO_ENCERRADA[caminho];
    if (fim && fim(resp, metodo)) await _limpaEstadoDoDispositivo();
    return resp;
  }

  /**
   * A request nossa, com a limpeza aplicada TAMBÉM quando não vem resposta.
   *
   * `await _origFetch(...)` estourava antes do `_comLimpeza`, então fetch que
   * REJEITA (offline, DNS, captive portal, abort) saía por baixo da limpeza —
   * e não é o caso raro: `logoutSettings` (settings.html) não tem
   * limpeza própria nenhuma e navega no `.finally` mesmo com a rejeição, então
   * sair do Ajustes em modo avião deixava `pigbank_menu_v1` (e-mail, nome,
   * plano), `pb_home_<uid>` e o Cache Storage inteiro no aparelho, com cara de
   * logout bem-sucedido. As limpezas locais de `dashboard.js` e `home.html`
   * cobrem só parte disso; o Cache Storage não é coberto por nenhuma.
   *
   * QUAL rota limpa sem resposta é decidido pelo predicado de cada uma
   * (`_SESSAO_ENCERRADA`), não aqui: só o logout. Rejeição no refresh e no
   * DELETE de conta é rede fora com a sessão de pé.
   */
  async function _requestComLimpeza(caminho, input, init) {
    let resp;
    try {
      resp = await _origFetch(input, init);
    } catch (e) {
      // Sem `try` em volta de propósito. Um `catch` aqui engoliria também o
      // erro de um predicado quebrado — a limpeza deixaria de acontecer sem
      // ninguém ficar sabendo, falha ABERTA, e a guarda de `null` de cada
      // predicado pararia de ser medida por teste nenhum. O que protege o erro
      // do chamador são as guardas, que falham fechado.
      await _comLimpeza(caminho, null, _metodo(input, init));
      throw e;
    }
    return _comLimpeza(caminho, resp, _metodo(input, init));
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
    let resp = await _requestComLimpeza(caminho, input, init);

    if (resp.status !== 401) return resp;

    // 401 no próprio refresh: não tenta de novo — caller redireciona pro login
    if (_isRefreshEndpoint(input)) return resp;

    // Tenta renovar e refazer a request original
    const ok = await _doRefresh();
    if (!ok) return resp;
    return _requestComLimpeza(caminho, input, init);
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
