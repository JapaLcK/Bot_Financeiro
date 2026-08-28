/**
 * frontend/open-finance-connect.js — escolher um banco e conectá-lo pelo
 * Open Finance (Pluggy). Extraído do settings.html para que a tela de
 * Configurações e o wizard de primeira configuração usem O MESMO código.
 *
 * O módulo não sabe o que é "Configurações" nem "onboarding": tudo que é
 * específico de tela entra por callback no `init`. É esse o seam — sem ele, a
 * alternativa era copiar ~550 linhas para a segunda tela.
 *
 *   PBOpenFinance.init({ apiBase, userId, onConnected, ... });
 *   PBOpenFinance.open();      // abre o modal na hora; preenche depois
 *   PBOpenFinance.destroy();   // remove markup e listeners; idempotente
 *
 * A ordem do original é preservada de propósito: o modal aparece SÍNCRONO e só
 * o preenchimento é assíncrono. O estado do plano é rebuscado em paralelo e
 * quem espera por ele é o confirmar — que é onde a decisão importa, porque é
 * dali que se abre a Pluggy.
 *
 * Servido por @router.get("/open-finance-connect.js") em
 * frontend/routes/static_pages.py — sem essa rota o arquivo dá 404 e o sintoma
 * só aparece no navegador (não há StaticFiles mount neste projeto).
 *
 * Sem framework e sem build, como o resto do frontend. Sem `onclick` inline: o
 * markup é montado com createElement/textContent e os cliques chegam por
 * delegação — o que também dispensa um helper de escape e tira handlers inline
 * do settings.html, na direção de fechar o 'unsafe-inline' do script-src.
 */
(function () {
  "use strict";

  /* ─── Configuração e estado ───────────────────────────────────────────── */

  var cfg = null;          // o que o host passou no init
  var root = null;         // nó do overlay, injetado sob demanda
  var onKeyRef = null;     // listener de teclado ativo (só enquanto aberto)
  var lastFocus = null;    // quem tinha o foco antes de abrir
  var connectors = null;   // cache da lista da Pluggy (não muda durante a sessão)
  var selected = null;     // {id, name}
  // Limites do plano e conexões, rebuscados a cada open() — confiar no que a
  // página leu no load faz quem acabou de fazer upgrade em outra aba continuar
  // batendo no teto antigo.
  //
  // A busca roda EM PARALELO com a abertura, não antes dela: quem espera é o
  // confirmar, que é onde a decisão importa (é ali que se abre a Pluggy e nasce
  // o risco de consentimento órfão). Bloquear a abertura punha dois fetches na
  // frente do modal e abria uma janela de corrida que o fluxo original não
  // tinha — ele mostrava o modal na hora e só o preenchimento era assíncrono.
  //
  // O resultado NÃO vira estado compartilhado: `refreshLimits()` devolve um
  // retrato, e quem decide usa o retrato que ele mesmo esperou. Com um objeto
  // mutável no módulo, duas buscas concorrentes (abre, fecha, abre) podiam
  // terminar fora de ordem e a mais VELHA sobrescrever a mais nova pouco antes
  // de o confirmar ler — decidindo teto e reconexão com dado vencido.
  var limitsPromise = null;   // busca em voo, iniciada no open()
  var ultimoTeto = null;      // só pro atalho do Free no open(); não é autoridade

  function noop() {}

  function conf(name, fallback) {
    return (cfg && typeof cfg[name] === "function") ? cfg[name] : (fallback || noop);
  }

  /* ─── Helpers de texto (vindos do settings.html sem mudança) ──────────── */

  function stripAccent(s) {
    return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  }

  function bankInitials(name) {
    var small = { de: 1, do: 1, da: 1, dos: 1, das: 1, e: 1, "-": 1 };
    var parts = (name || "").replace(/[^\wÀ-ÿ\s-]/g, "").split(/\s+/)
      .filter(function (w) { return w && !small[w.toLowerCase()]; });
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return (name || "?").replace(/[^A-Za-zÀ-ÿ]/g, "").slice(0, 2).toUpperCase();
  }

  function bankColor(hex) {
    var c = String(hex || "").replace("#", "");
    return /^[0-9a-f]{6}$/i.test(c) ? "#" + c : "#5f6470";
  }

  /* ─── Rede ────────────────────────────────────────────────────────────── */

  function headers(extra) {
    // Implementação única, do auth-refresh.js, que as duas páginas carregam.
    return window.pbCsrfHeaders ? window.pbCsrfHeaders(extra) : (extra || {});
  }

  function url(path) {
    return (cfg && cfg.apiBase ? cfg.apiBase : "") + path;
  }

  /**
   * O `detail` do FastAPI vem string OU objeto estruturado — o limite de bancos
   * por plano, por exemplo, chega como {code:"OF_BANK_LIMIT", message:"… /precos"}.
   * Aceitar só string jogaria fora justamente a mensagem acionável e mostraria
   * um genérico no lugar. Mesma regra do readApiError() que havia no
   * settings.html antes desta extração.
   */
  async function apiError(resp) {
    var raw = "";
    try { raw = await resp.text(); } catch (e) { /* corpo ilegível */ }
    try {
      var detail = JSON.parse(raw).detail;
      if (detail && typeof detail === "object") return new Error(detail.message || detail.code || raw);
      return new Error(detail || raw);
    } catch (e) {
      return new Error(raw || "Erro inesperado");
    }
  }

  async function get(path) {
    var resp = await fetch(url(path), { credentials: "same-origin", headers: headers() });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  async function post(path, body) {
    var resp = await fetch(url(path), {
      method: "POST",
      credentials: "same-origin",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body || {}),
    });
    if (!resp.ok) throw await apiError(resp);
    return resp.json();
  }

  /** Conexões que contam no teto — espelha o backend: PAUSED não conta. */
  function countsTowardLimit(conn) {
    return String((conn && conn.status) || "").toUpperCase() !== "PAUSED";
  }

  /**
   * Retrato do teto do plano e das conexões, devolvido a quem pediu — nunca
   * escrito num objeto do módulo, pra duas buscas concorrentes não terminarem
   * fora de ordem e a mais velha decidir pela mais nova.
   *
   * Devolve null se qualquer um dos dois lados não vier, e o confirmar trata
   * null como "não dá pra seguir". Falha aqui não pode virar permissão: com um
   * default permissivo, quem está no teto autorizaria a instituição na Pluggy
   * antes de o /pluggy-item recusar com 402, deixando o consentimento órfão.
   */
  async function refreshLimits() {
    var me, of;
    try {
      var both = await Promise.all([
        get("/auth/me"),
        get("/open-finance/" + cfg.userId),
      ]);
      me = both[0];
      of = both[1];
    } catch (e) {
      return null;
    }

    var list = (of && of.connections) || [];
    var counted = list.filter(countsTowardLimit);
    return {
      banksMax: (me && me.of_banks_max !== undefined) ? me.of_banks_max : null,
      hasConnection: list.length > 0,
      count: counted.length,
      names: counted.map(function (c) { return stripAccent(c.institution_name || ""); }),
    };
  }

  /* ─── Markup ──────────────────────────────────────────────────────────── */

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function buildRoot() {
    var overlay = el("div", "bankpick-overlay");
    overlay.id = "bankpick-overlay";

    var modal = el("div", "bankpick-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Conectar banco");

    var head = el("div", "bankpick-head");
    var headRow = el("div", "bankpick-head-row");
    var titles = el("div");
    titles.appendChild(el("div", "bankpick-title", "Conectar banco"));
    titles.appendChild(el("div", "bankpick-sub", "Busque entre as instituições disponíveis"));
    var x = el("button", "bankpick-x");
    x.type = "button";
    x.setAttribute("aria-label", "Fechar");
    x.setAttribute("data-act", "close");
    x.innerHTML = '<i class="ph ph-x" aria-hidden="true"></i>';
    headRow.appendChild(titles);
    headRow.appendChild(x);

    var searchWrap = el("div", "bankpick-search-wrap");
    var icon = el("span");
    icon.setAttribute("aria-hidden", "true");
    icon.innerHTML = '<i class="ph ph-magnifying-glass" aria-hidden="true"></i>';
    var search = el("input");
    search.id = "bankpick-search";
    search.type = "text";
    search.autocomplete = "off";
    search.placeholder = "Buscar banco (ex: Nubank, Itaú, C6…)";
    searchWrap.appendChild(icon);
    searchWrap.appendChild(search);

    head.appendChild(headRow);
    head.appendChild(searchWrap);

    var list = el("div", "bankpick-list");
    list.id = "bankpick-list";

    var foot = el("div", "bankpick-foot");
    var count = el("div", "bankpick-count", "Nenhum banco selecionado");
    count.id = "bankpick-count";
    var go = el("button", "btn-connect bankpick-go", "Conectar Open Finance");
    go.id = "bankpick-go";
    go.type = "button";
    go.disabled = true;
    go.setAttribute("data-act", "confirm");
    foot.appendChild(count);
    foot.appendChild(go);

    modal.appendChild(head);
    modal.appendChild(list);
    modal.appendChild(foot);
    overlay.appendChild(modal);

    // Delegação: um listener no root, nenhum handler inline.
    overlay.addEventListener("click", onClick);
    search.addEventListener("input", function () { renderList(search.value); });
    return overlay;
  }

  function ensureRoot() {
    if (root && document.contains(root)) return root;
    root = buildRoot();
    document.body.appendChild(root);
    return root;
  }

  function q(sel) { return root ? root.querySelector(sel) : null; }

  /* ─── Render da lista ─────────────────────────────────────────────────── */

  function renderList(filter) {
    var listEl = q("#bankpick-list");
    if (!listEl) return;
    var f = stripAccent(filter || "").trim();
    var items = (connectors || []).filter(function (b) {
      return stripAccent(b.name).indexOf(f) !== -1;
    });

    listEl.innerHTML = "";
    if (!items.length) {
      listEl.appendChild(el("div", "bankpick-empty",
        'Nenhum banco encontrado para "' + (filter || "") + '".'));
      return;
    }

    var letter = "";
    items.forEach(function (b) {
      var L = (b.name[0] || "#").toUpperCase();
      if (L !== letter) {
        letter = L;
        listEl.appendChild(el("div", "bankpick-alpha", L));
      }
      var on = selected && selected.id === b.id;
      var row = el("button", "bank-row bankpick-row" + (on ? " active" : ""));
      row.type = "button";
      row.setAttribute("data-name", b.name);
      row.setAttribute("data-id", String(b.id));

      var logo = el("span", "bank-logo bankpick-logo", bankInitials(b.name));
      logo.style.color = bankColor(b.color);
      if (b.logo) {
        var img = document.createElement("img");
        img.src = b.logo;
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", function () { img.remove(); });
        logo.appendChild(img);
      }

      var info = el("span", "bank-info");
      info.appendChild(el("span", "bank-name", b.name));
      info.appendChild(el("span", "bank-meta",
        b.inv ? "Conta, cartão · caixinha/investimentos" : "Conta corrente e cartão"));

      var check = el("span", "bank-check");
      if (on) check.innerHTML = '<i class="ph ph-check" aria-hidden="true"></i>';

      row.appendChild(logo);
      row.appendChild(info);
      row.appendChild(check);
      listEl.appendChild(row);
    });
  }

  function pick(id, node) {
    selected = { id: id, name: node ? node.getAttribute("data-name") : "" };
    var rows = root ? root.querySelectorAll("#bankpick-list .bank-row") : [];
    Array.prototype.forEach.call(rows, function (r) {
      var on = r === node;
      r.classList.toggle("active", on);
      var chk = r.querySelector(".bank-check");
      if (chk) chk.innerHTML = on ? '<i class="ph ph-check" aria-hidden="true"></i>' : "";
    });
    syncFoot();
  }

  function syncFoot() {
    var count = q("#bankpick-count");
    var go = q("#bankpick-go");
    if (!count || !go) return;
    if (selected) {
      count.textContent = "";
      count.appendChild(document.createTextNode("Selecionado: "));
      count.appendChild(el("strong", null, selected.name));
      go.disabled = false;
    } else {
      count.textContent = "Nenhum banco selecionado";
      go.disabled = true;
    }
  }

  /* ─── Teclado: o trap que o aria-modal promete ────────────────────────── */

  function focusables() {
    var modal = q(".bankpick-modal");
    if (!modal) return [];
    var sel = 'a[href], button:not([disabled]), input:not([disabled]),'
            + ' select:not([disabled]), textarea:not([disabled]),'
            + ' [tabindex]:not([tabindex="-1"])';
    return Array.prototype.filter.call(modal.querySelectorAll(sel), function (e) {
      return e.offsetWidth > 0 || e.offsetHeight > 0 || e.getClientRects().length > 0;
    });
  }

  function onKey(e) {
    if (e.key === "Escape") { close(); return; }
    if (e.key !== "Tab") return;

    var modal = q(".bankpick-modal");
    var alvos = focusables();
    if (!modal || !alvos.length) return;

    var primeiro = alvos[0];
    var ultimo = alvos[alvos.length - 1];
    var proximo = e.shiftKey ? ultimo : primeiro;
    var borda = e.shiftKey ? primeiro : ultimo;

    // `foco fora` cobre o caso de já ter escapado (clique no fundo, foco no body)
    if (!modal.contains(document.activeElement) || document.activeElement === borda) {
      e.preventDefault();
      proximo.focus();
    }
  }

  /* ─── Cliques (delegados) ─────────────────────────────────────────────── */

  function onClick(e) {
    if (e.target === root) { close(); return; }          // clique no fundo
    var act = e.target.closest ? e.target.closest("[data-act]") : null;
    if (act) {
      if (act.getAttribute("data-act") === "close") return close();
      if (act.getAttribute("data-act") === "confirm") return confirmPick();
    }
    var row = e.target.closest ? e.target.closest(".bank-row") : null;
    if (row) pick(Number(row.getAttribute("data-id")), row);
  }

  /* ─── Abrir / fechar ──────────────────────────────────────────────────── */

  /**
   * Abre o picker. SÍNCRONO até o modal estar na tela — nada de await antes,
   * como no fluxo original: o modal aparece na hora e só o preenchimento é
   * assíncrono. Pôr fetch na frente disso criava uma janela em que um segundo
   * clique disparava tudo de novo e o `lastFocus` era capturado depois do foco
   * já ter ido pro campo de busca.
   */
  function open() {
    if (!cfg) return;

    // Free (limite 0): mesma defesa do original — o applyOfConnectButton do
    // host já roteia o botão pra /precos nesse caso.
    if (ultimoTeto === 0) { window.location.href = "/precos"; return; }

    ensureRoot();
    lastFocus = document.activeElement;
    root.classList.add("open");
    // Registrado aqui, antes dos returns/await abaixo, pra valer em TODOS os
    // caminhos: lista em cache, lista carregada do servidor e erro de fetch.
    onKeyRef = onKey;
    document.addEventListener("keydown", onKeyRef);

    var search = q("#bankpick-search");
    if (search) { search.value = ""; search.focus(); }
    selected = null;
    syncFoot();

    // Estado do plano em paralelo: quem espera é o confirmar.
    limitsPromise = refreshLimits().then(function (snap) {
      if (snap) ultimoTeto = snap.banksMax;   // alimenta só o atalho da próxima abertura
      return snap;
    });

    if (connectors) { renderList(""); return; }
    carregarBancos();
  }

  /**
   * Preenche a lista. Depois do await não recria nada: se o modal foi destruído
   * no meio, `q()` devolve null com o root ausente e tudo aqui sai cedo — é o
   * que dispensa qualquer controle de ciclo de vida.
   */
  async function carregarBancos() {
    var listEl = q("#bankpick-list");
    if (listEl) {
      listEl.innerHTML = "";
      listEl.appendChild(el("div", "bankpick-empty", "Carregando bancos…"));
    }
    try {
      var data = await get("/open-finance/" + cfg.userId + "/connectors");
      connectors = (data.connectors || []).filter(function (b) { return b && b.name; });
      renderList("");
      // sem focar de novo: o foco já entrou no modal antes do await, e refocar
      // agora roubaria o foco de quem já tivesse tabulado pra dentro do card
    } catch (err) {
      var alvo = q("#bankpick-list");
      if (alvo) {
        alvo.innerHTML = "";
        alvo.appendChild(el("div", "bankpick-empty",
          "Não deu pra carregar a lista de bancos. Tente de novo."));
      }
    }
  }

  function close() {
    if (root) root.classList.remove("open");
    if (onKeyRef) { document.removeEventListener("keydown", onKeyRef); onKeyRef = null; }

    // devolve o foco pra quem abriu, senão o teclado volta pro topo da página
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    lastFocus = null;
  }

  /**
   * É AQUI que o estado do plano precisa estar fresco: o passo seguinte abre a
   * Pluggy, e é dali que sai o consentimento órfão se o teto já estiver cheio.
   * Por isso o await mora neste ponto, e não na abertura do modal.
   */
  async function confirmPick() {
    if (!selected) return;
    var go = q("#bankpick-go");
    // Desabilita durante a espera: sem isso um segundo clique passaria antes de
    // os limites chegarem. Na prática a busca começou lá no open() e já
    // resolveu, mas "na prática" não é garantia.
    if (go) go.disabled = true;
    try {
      // O retrato que ESTE confirmar esperou — não um objeto de módulo que uma
      // busca mais velha pudesse ter sobrescrito no caminho.
      var plano = await (limitsPromise || refreshLimits());
      if (!plano) {
        conf("notify")("Não deu pra confirmar seu plano agora. Tente de novo em instantes.", "error");
        return;
      }
      if (plano.banksMax === 0) { window.location.href = "/precos"; return; }

      // Teto atingido: só segue se for RECONEXÃO de um banco já conectado (mesmo
      // nome). Banco novo abriria o widget da Pluggy só pra tomar 402 no
      // /pluggy-item — deixando item e consentimento órfãos. Bloqueia antes.
      if (plano.banksMax !== null && plano.banksMax > 0 && plano.count >= plano.banksMax) {
        var isReconnect = plano.names.indexOf(stripAccent(selected.name || "")) !== -1;
        if (!isReconnect) {
          var n = plano.banksMax;
          conf("notify")("Seu plano conecta até " + n + " banco" + (n > 1 ? "s" : "") +
                         ". Faça upgrade pra conectar mais: /precos", "error");
          return;
        }
      }
      close();
      connect();
    } finally {
      if (go) go.disabled = false;
    }
  }

  /* ─── Widget da Pluggy ────────────────────────────────────────────────── */

  function pluggyFactory() {
    return window.PluggyConnect || window.pluggyConnect || window.PluggyConnectWidget;
  }

  /**
   * `uid` é o usuário de QUEM INICIOU o fluxo, fixado lá no connect(). Ler
   * `cfg.userId` aqui gravaria o item autorizado por A na conta de B se um
   * init() trocasse de usuário enquanto o widget estava aberto — e isolamento
   * por usuário é regra dura deste repositório.
   */
  async function savePluggyItem(itemData, uid) {
    var item = (itemData && itemData.item) || itemData || {};
    if (!item.id && !item.itemId) throw new Error("A Pluggy não retornou o item conectado.");
    // Só depois desta resposta a conexão existe do lado do PigBank — o
    // onSuccess da Pluggy diz apenas que o banco autorizou.
    var data = await post("/open-finance/" + uid + "/pluggy-item", { item: item });
    conf("onConnected")(data);
    return data;
  }

  async function openWidget(uid) {
    var data = await post("/open-finance/" + uid + "/connect-token", {});
    var accessToken = data.accessToken;
    if (!accessToken) throw new Error("A Pluggy não retornou accessToken.");

    var PluggyConnect = pluggyFactory();
    if (!PluggyConnect) {
      throw new Error("Widget da Pluggy não carregou. Recarregue a página e tente novamente.");
    }

    var connectorTypes = ["PERSONAL_BANK", "BUSINESS_BANK"];
    if (data.includeSandbox) connectorTypes.push("SANDBOX");

    var widget = new PluggyConnect({
      connectToken: accessToken,
      includeSandbox: Boolean(data.includeSandbox),
      connectorTypes: connectorTypes,
      selectedConnectorId: (selected && selected.id) || undefined,
      // INVESTMENTS incluído: o sync lê /investments pra achar a Caixinha
      // (FIXED_INCOME/CDB). Precisa casar com PLUGGY_PRODUCTS do connect-token,
      // senão o produto não é coletado.
      products: ["ACCOUNTS", "TRANSACTIONS", "CREDIT_CARDS", "INVESTMENTS"],
      language: "pt",
      theme: "dark",
      onSuccess: async function (itemData) {
        try {
          await savePluggyItem(itemData, uid);
          conf("notify")("Banco conectado com sucesso!", "ok");
        } catch (err) {
          // Ex.: limite de bancos do plano (402 OF_BANK_LIMIT) — o banco foi
          // autorizado na Pluggy mas o plano não comporta mais conexões.
          conf("onError")(String((err && err.message) || err) || "Não foi possível salvar a conexão.");
        }
      },
      onError: function (error) {
        console.error("Pluggy Connect error", error);
        conf("onError")("Não foi possível concluir a conexão.");
      },
      onClose: function () {
        conf("onClose")();
      },
    });

    if (typeof widget.init === "function") widget.init();
    else if (typeof widget.open === "function") widget.open();
    else throw new Error("Widget da Pluggy carregou em um formato inesperado.");
  }

  async function connect() {
    // Fixa o usuário AQUI e carrega ele por todo o fluxo: o widget da Pluggy
    // fica aberto por minutos, e reler cfg.userId depois gravaria o item de
    // quem iniciou na conta de quem estiver configurado no fim.
    var uid = cfg && cfg.userId;
    if (!uid) { conf("onError")("Sessão inválida. Faça login novamente"); return; }
    conf("onConnectStart")();
    try {
      await openWidget(uid);
    } catch (err) {
      conf("onError")((err && err.message) || "Erro ao abrir a conexão Pluggy");
    }
  }

  /* ─── API pública ─────────────────────────────────────────────────────── */

  function init(options) {
    // Idempotente: chamar de novo troca a configuração e NÃO soma listener —
    // os listeners vivem no root (um só, criado sob demanda) e no document
    // apenas enquanto o modal está aberto.
    cfg = options || {};
    return api;
  }

  function destroy() {
    // Não precisa invalidar nada em voo: nenhum caminho assíncrono recria o
    // root. O único await que sobra é o da lista de bancos, e ele só escreve
    // via q(), que devolve null quando o root não existe mais.
    close();
    if (root && root.parentNode) root.parentNode.removeChild(root);
    root = null;
    selected = null;
    // `connectors` é cache de dado remoto que não muda entre telas; mantido de
    // propósito pra reabrir sem novo fetch da lista.
  }

  var api = { init: init, open: open, close: close, destroy: destroy };
  window.PBOpenFinance = api;
})();
